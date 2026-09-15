using System.Diagnostics;
using System.Text;
using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Engine;
using EQRisk.Infrastructure.Native;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Jobs;

/// <summary>
/// Runs <c>eqrisk</c> commands one at a time as child processes of the app: output streams to a log
/// file and to listeners, the history goes to SQLite, and each process lives in a job object so a
/// stop (or the app ending) ends everything it started. Keys from the vault are placed in the
/// environment only of jobs that call a vendor, and are never logged.
/// </summary>
public sealed partial class JobRunner : IJobRunner, IDisposable
{
    private const int LiveTextLimit = 512 * 1024;
    private static readonly UTF8Encoding Utf8 = new(encoderShouldEmitUTF8Identifier: false);

    private readonly IEngineLocator locator;
    private readonly ISettingsStore settings;
    private readonly IJobStore store;
    private readonly IKeyVault vault;
    private readonly IEngineProcessProbe probe;
    private readonly AppPaths paths;
    private readonly TimeProvider clock;
    private readonly ILogger<JobRunner> log;
    private readonly string appDirectory;
    private readonly SemaphoreSlim startLock = new(1, 1);
    private Running? running;
    private TaskCompletionSource<JobRecord>? lastCompletion;

    public JobRunner(IEngineLocator locator, ISettingsStore settings, IJobStore store, IKeyVault vault,
        IEngineProcessProbe probe, AppPaths paths, TimeProvider clock, ILogger<JobRunner> log, string appDirectory)
    {
        this.locator = locator;
        this.settings = settings;
        this.store = store;
        this.vault = vault;
        this.probe = probe;
        this.paths = paths;
        this.clock = clock;
        this.log = log;
        this.appDirectory = appDirectory;
    }

    public JobRecord? Current => running?.Record;

    public IReadOnlyCollection<int> OwnProcessIds => running is { } r ? [r.ProcessId] : [];

    /// <summary>The text the running job has written so far (the latest half megabyte).</summary>
    public string LiveText => running?.Text() ?? "";

    /// <summary>How a job becomes a process. Tests replace it with a stand-in command.</summary>
    internal Func<EngineInfo, JobSpec, ProcessStartInfo> CommandBuilder { get; set; } = DefaultCommand;

    public event EventHandler<JobRecord>? JobChanged;

    public event EventHandler<JobOutput>? Output;

    public async Task<JobRecord> StartAsync(JobSpec spec, JobTrigger trigger, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(spec);
        await startLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (running is { } busy)
            {
                throw new JobBusyException($"{busy.Record.Label} is running. Wait for it, or stop it first.");
            }

            var external = probe.FindJobs([]);
            if (external.Count > 0)
            {
                throw new JobBusyException(
                    $"An eqrisk job started outside the app is running (process {external[0].ProcessId}). " +
                    "Wait for it to finish.");
            }

            var check = locator.Locate(settings.Current.EngineRoot, appDirectory);
            if (!check.Ok)
            {
                throw new InvalidOperationException(check.Problem);
            }

            return await LaunchAsync(check.Engine!, spec, trigger, ct).ConfigureAwait(false);
        }
        finally
        {
            startLock.Release();
        }
    }

    /// <summary>The end of the running job, or of the last one if it already ended: a job that fails at
    /// once must not look like "no job" to a caller that asks a moment later.</summary>
    public async Task<JobRecord?> WaitAsync(CancellationToken ct = default) =>
        lastCompletion is { } last ? await last.Task.WaitAsync(ct).ConfigureAwait(false) : null;

    public async Task StopAsync()
    {
        if (running is not { } r)
        {
            return;
        }

        r.StopRequested = true;
        LogStopping(r.Record.Label);
        r.Job.Terminate();
        await r.Completion.Task.ConfigureAwait(false);
    }

    public async Task<string> ReadLogAsync(JobRecord job, int maxChars, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(job);
        if (!File.Exists(job.LogPath))
        {
            return "";
        }

        await using var stream = new FileStream(job.LogPath, FileMode.Open, FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        var maxBytes = (long)maxChars * 4;             // UTF-8 uses at most four bytes a character
        if (stream.Length > maxBytes)
        {
            stream.Seek(-maxBytes, SeekOrigin.End);
        }

        using var reader = new StreamReader(stream, Utf8);
        var text = await reader.ReadToEndAsync(ct).ConfigureAwait(false);
        return text.Length > maxChars ? text[^maxChars..] : text;
    }

    public void Dispose()
    {
        running?.Job.Dispose();
        startLock.Dispose();
    }

    private async Task<JobRecord> LaunchAsync(EngineInfo engine, JobSpec spec, JobTrigger trigger, CancellationToken ct)
    {
        paths.EnsureCreated();
        var started = clock.GetUtcNow();
        var logPath = Path.Combine(paths.JobLogs,
            $"{started.ToLocalTime():yyyyMMdd-HHmmss}-{spec.Kind.ToString().ToLowerInvariant()}.log");
        var record = await store.AddAsync(new JobRecord(0, spec.Kind, spec.Label, spec.ArgumentText,
            JobStatus.Running, trigger, started, null, null, logPath), ct).ConfigureAwait(false);

        var psi = CommandBuilder(engine, spec);
        var keys = spec.NeedsKeys ? vault.ReadForProcess(KeyCatalog.Engine.Select(k => k.Env)) : null;
        EngineEnvironment.Prepare(psi.Environment, keys, scrubInheritedKeys: !spec.NeedsKeys);
        if (log.IsEnabled(LogLevel.Information))
        {
            var command = spec.CommandLine;
            var note = KeyNote(spec, keys);                         // key names only, never values
            LogStarting(command, note);
        }

        var writer = new StreamWriter(new FileStream(logPath, FileMode.Create, FileAccess.Write,
            FileShare.ReadWrite | FileShare.Delete), Utf8) { AutoFlush = true };
        await writer.WriteLineAsync($"# {spec.CommandLine}").ConfigureAwait(false);
        await writer.WriteLineAsync($"# started {started.ToLocalTime():yyyy-MM-dd HH:mm:ss} in {engine.Root}")
            .ConfigureAwait(false);

        var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var jobObject = new JobObject();
        try
        {
            process.Start();
            jobObject.Assign(process);
            ProcessPriority.Normal(process);
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            await writer.WriteLineAsync($"# could not start: {ex.Message}").ConfigureAwait(false);
            await writer.DisposeAsync().ConfigureAwait(false);
            jobObject.Dispose();
            process.Dispose();
            var failed = record with { Status = JobStatus.Failed, FinishedAt = clock.GetUtcNow() };
            await store.UpdateAsync(failed, ct).ConfigureAwait(false);
            throw;
        }

        var r = new Running(record, process, jobObject, writer);
        process.OutputDataReceived += (_, e) => OnLine(r, e.Data);
        process.ErrorDataReceived += (_, e) => OnLine(r, e.Data);
        running = r;
        lastCompletion = r.Completion;
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        JobChanged?.Invoke(this, record);
        _ = Task.Run(() => MonitorAsync(r), CancellationToken.None);
        return record;
    }

    private void OnLine(Running r, string? line)
    {
        if (line is null)
        {
            return;
        }

        r.Append(line);
        Output?.Invoke(this, new JobOutput(r.Record.Id, line + "\n"));
    }

    private async Task MonitorAsync(Running r)
    {
        await r.Process.WaitForExitAsync().ConfigureAwait(false);
        var code = r.Process.ExitCode;
        var status = r.StopRequested ? JobStatus.Stopped : code == 0 ? JobStatus.Succeeded : JobStatus.Failed;
        var finished = r.Record with { Status = status, FinishedAt = clock.GetUtcNow(), ExitCode = code };
        r.Finish($"# {status.ToString().ToLowerInvariant()} with exit code {code} at {finished.FinishedAt:u}");
        try
        {
            await store.UpdateAsync(finished).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException)
        {
            LogHistoryFailed(ex.Message);
        }

        LogFinished(finished.Label, status, code);
        r.Job.Dispose();
        r.Process.Dispose();
        running = null;
        JobChanged?.Invoke(this, finished);
        r.Completion.TrySetResult(finished);
    }

    private static string KeyNote(JobSpec spec, IReadOnlyDictionary<string, string>? keys) =>
        !spec.NeedsKeys ? "no keys"
        : keys is { Count: > 0 } ? "keys from the vault: " + string.Join(", ", keys.Keys.Order(StringComparer.Ordinal))
        : "keys from .env";

    private static ProcessStartInfo DefaultCommand(EngineInfo engine, JobSpec spec)
    {
        var psi = new ProcessStartInfo(engine.PythonExe)
        {
            WorkingDirectory = engine.Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Utf8,
            StandardErrorEncoding = Utf8,
        };
        psi.ArgumentList.Add("-m");
        psi.ArgumentList.Add("eqrisk.cli");
        foreach (var arg in spec.Arguments)
        {
            psi.ArgumentList.Add(arg);
        }

        return psi;
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Job starting: {Command} ({Keys})")]
    private partial void LogStarting(string command, string keys);

    [LoggerMessage(Level = LogLevel.Information, Message = "Job finished: {Label} {Status} (exit code {Code})")]
    private partial void LogFinished(string label, JobStatus status, int code);

    [LoggerMessage(Level = LogLevel.Information, Message = "Stopping job: {Label}")]
    private partial void LogStopping(string label);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Could not record the job's end in the history: {Reason}")]
    private partial void LogHistoryFailed(string reason);

    private sealed class Running(JobRecord record, Process process, JobObject job, StreamWriter writer)
    {
        private readonly Lock gate = new();
        private readonly StringBuilder text = new();

        public JobRecord Record { get; } = record;

        public Process Process { get; } = process;

        public JobObject Job { get; } = job;

        public int ProcessId { get; } = process.Id;

        public TaskCompletionSource<JobRecord> Completion { get; } =
            new(TaskCreationOptions.RunContinuationsAsynchronously);

        public bool StopRequested { get; set; }

        public void Append(string line)
        {
            lock (gate)
            {
                writer.WriteLine(line);
                text.Append(line).Append('\n');
                if (text.Length > LiveTextLimit)
                {
                    text.Remove(0, text.Length - LiveTextLimit);
                }
            }
        }

        public string Text()
        {
            lock (gate)
            {
                return text.ToString();
            }
        }

        public void Finish(string footer)
        {
            lock (gate)
            {
                writer.WriteLine(footer);
                writer.Dispose();
            }
        }
    }
}
