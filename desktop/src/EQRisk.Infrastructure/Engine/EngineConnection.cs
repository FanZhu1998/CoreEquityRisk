using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using EQRisk.Core.Engine;
using EQRisk.Core.Feed;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Native;
using Microsoft.Extensions.Logging;
using Polly;
using Polly.Retry;

namespace EQRisk.Infrastructure.Engine;

/// <summary>
/// Keeps one <c>eqrisk serve</c> process running and talks to it in JSON lines
/// (eqrisk/pipeline/feed.py). The process holds no API keys, belongs to a job object so it ends
/// with the app, and is started again with backoff (Polly) when it exits. Every read is idempotent,
/// so a call that loses its process is retried once on a fresh one.
/// </summary>
public sealed partial class EngineConnection : IEngineConnection, IAsyncDisposable
{
    /// <summary>Python imports the model stack before answering; a cold start takes several seconds.</summary>
    public static readonly TimeSpan StartTimeout = TimeSpan.FromSeconds(120);

    private const int StderrLinesKept = 40;
    private static readonly UTF8Encoding Utf8 = new(encoderShouldEmitUTF8Identifier: false);

    private readonly IEngineLocator locator;
    private readonly ISettingsStore settings;
    private readonly ILogger<EngineConnection> log;
    private readonly string appDirectory;
    private readonly ResiliencePipeline startPipeline;
    private readonly ResiliencePipeline callPipeline;
    private readonly SemaphoreSlim startLock = new(1, 1);
    private readonly SemaphoreSlim writeLock = new(1, 1);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> pending = new();
    private readonly Queue<string> stderrTail = new();
    private readonly Lock tailLock = new();
    private Process? process;
    private JobObject? job;
    private TaskCompletionSource ready = NewReady();
    private long nextId;
    private int disposed;

    public EngineConnection(IEngineLocator locator, ISettingsStore settings, ILogger<EngineConnection> log,
        string appDirectory)
    {
        this.locator = locator;
        this.settings = settings;
        this.log = log;
        this.appDirectory = appDirectory;
        startPipeline = new ResiliencePipelineBuilder()
            .AddRetry(new RetryStrategyOptions
            {
                ShouldHandle = new PredicateBuilder().Handle<EngineUnavailableException>(),
                MaxRetryAttempts = 2,
                BackoffType = DelayBackoffType.Exponential,
                Delay = TimeSpan.FromSeconds(2),
                OnRetry = args =>
                {
                    LogRestarting(args.AttemptNumber + 1, args.Outcome.Exception?.Message);
                    return ValueTask.CompletedTask;
                },
            })
            .Build();
        callPipeline = new ResiliencePipelineBuilder()
            .AddRetry(new RetryStrategyOptions
            {
                ShouldHandle = new PredicateBuilder().Handle<EngineUnavailableException>(),
                MaxRetryAttempts = 1,
                Delay = TimeSpan.FromMilliseconds(200),
            })
            .Build();
        settings.Changed += OnSettingsChanged;
    }

    public EngineConnectionState State { get; private set; } = EngineConnectionState.Stopped;

    public string? LastError { get; private set; }

    /// <summary>What the engine said when it started: version, model, protocol.</summary>
    public EngineHello? Hello { get; private set; }

    public int? ProcessId
    {
        get
        {
            var p = process;
            return p is not null && !p.HasExited ? p.Id : null;
        }
    }

    public event EventHandler<EngineConnectionState>? StateChanged;

    public async Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?>? parameters,
        TimeSpan timeout, CancellationToken ct)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(method);
        ObjectDisposedException.ThrowIf(Volatile.Read(ref disposed) != 0, this);
        return await callPipeline.ExecuteAsync(async token =>
        {
            await EnsureStartedAsync(token).ConfigureAwait(false);
            return await SendAsync(method, parameters, timeout, token).ConfigureAwait(false);
        }, ct).ConfigureAwait(false);
    }

    /// <summary>Start now rather than on the first call (the app does this at start-up).</summary>
    public Task StartAsync(CancellationToken ct) => EnsureStartedAsync(ct);

    /// <summary>Stop the process; the next call starts a new one (after the engine folder changes).</summary>
    public async Task RestartAsync(CancellationToken ct)
    {
        await startLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            await StopProcessAsync().ConfigureAwait(false);
            SetState(EngineConnectionState.Stopped, null);
        }
        finally
        {
            startLock.Release();
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0)
        {
            return;
        }

        settings.Changed -= OnSettingsChanged;
        await StopProcessAsync().ConfigureAwait(false);
        SetState(EngineConnectionState.Stopped, null);
        startLock.Dispose();
        writeLock.Dispose();
    }

    private async Task EnsureStartedAsync(CancellationToken ct)
    {
        if (IsRunning())
        {
            return;
        }

        await startLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (!IsRunning())
            {
                await startPipeline.ExecuteAsync(async token => await StartProcessAsync(token).ConfigureAwait(false), ct)
                    .ConfigureAwait(false);
            }
        }
        finally
        {
            startLock.Release();
        }
    }

    private bool IsRunning() => State == EngineConnectionState.Ready && process is { HasExited: false };

    private async Task StartProcessAsync(CancellationToken ct)
    {
        var check = locator.Locate(settings.Current.EngineRoot, appDirectory);
        if (!check.Ok)
        {
            SetState(EngineConnectionState.NotConfigured, check.Problem);
            throw new EngineException("NotConfigured", check.Problem ?? "The engine folder is not set.");
        }

        var engine = check.Engine!;
        await StopProcessAsync().ConfigureAwait(false);
        SetState(Hello is null ? EngineConnectionState.Starting : EngineConnectionState.Restarting, null);

        var psi = new ProcessStartInfo(engine.PythonExe)
        {
            WorkingDirectory = engine.Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardInputEncoding = Utf8,        // no byte-order mark: it would corrupt the first request
            StandardOutputEncoding = Utf8,
            StandardErrorEncoding = Utf8,
        };
        foreach (var arg in (string[])["-m", "eqrisk.cli", "serve", "--root", engine.Root])
        {
            psi.ArgumentList.Add(arg);
        }

        EngineEnvironment.Prepare(psi.Environment, keys: null, scrubInheritedKeys: true);

        var started = new Process { StartInfo = psi, EnableRaisingEvents = true };
        var jobObject = new JobObject();
        ready = NewReady();
        lock (tailLock)
        {
            stderrTail.Clear();
        }

        try
        {
            started.Start();
            jobObject.Assign(started);
            ProcessPriority.Normal(started);
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            jobObject.Dispose();
            started.Dispose();
            SetState(EngineConnectionState.Failed, ex.Message);
            throw new EngineUnavailableException($"The engine could not start: {ex.Message}", ex);
        }

        started.StandardInput.NewLine = "\n";
        process = started;
        job = jobObject;
        started.Exited += (_, _) => OnExited(started);
        _ = Task.Run(() => ReadOutputAsync(started), CancellationToken.None);
        _ = Task.Run(() => ReadErrorsAsync(started), CancellationToken.None);
        LogStarting(engine.Root, started.Id);

        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(StartTimeout);
        try
        {
            await ready.Task.WaitAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            SetState(EngineConnectionState.Failed, "The engine did not become ready in time.");
            throw new EngineUnavailableException($"The engine did not become ready within {StartTimeout.TotalSeconds:0} s.");
        }

        SetState(EngineConnectionState.Ready, null);
    }

    private async Task<JsonElement> SendAsync(string method, IReadOnlyDictionary<string, object?>? parameters,
        TimeSpan timeout, CancellationToken ct)
    {
        var p = process ?? throw new EngineUnavailableException("The engine is not running.");
        var id = Interlocked.Increment(ref nextId);
        var reply = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = reply;
        try
        {
            var line = JsonSerializer.Serialize(new Request(id, method, parameters), FeedJson.Options);
            await writeLock.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                await p.StandardInput.WriteLineAsync(line.AsMemory(), ct).ConfigureAwait(false);
                await p.StandardInput.FlushAsync(ct).ConfigureAwait(false);
            }
            catch (IOException ex)
            {
                throw new EngineUnavailableException("The engine stopped while a request was being sent.", ex);
            }
            finally
            {
                writeLock.Release();
            }

            try
            {
                return await reply.Task.WaitAsync(timeout, ct).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                throw new TimeoutException($"The engine did not answer '{method}' within {timeout.TotalSeconds:0} s.");
            }
        }
        finally
        {
            pending.TryRemove(id, out _);
        }
    }

    private async Task ReadOutputAsync(Process p)
    {
        try
        {
            while (await p.StandardOutput.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                Dispatch(line);
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException or InvalidOperationException)
        {
            LogStreamClosed(ex.Message);
        }
    }

    private async Task ReadErrorsAsync(Process p)
    {
        try
        {
            while (await p.StandardError.ReadLineAsync().ConfigureAwait(false) is { } line)
            {
                lock (tailLock)
                {
                    stderrTail.Enqueue(line);
                    while (stderrTail.Count > StderrLinesKept)
                    {
                        stderrTail.Dequeue();
                    }
                }

                LogEngineLine(line);
            }
        }
        catch (Exception ex) when (ex is IOException or ObjectDisposedException or InvalidOperationException)
        {
            LogStreamClosed(ex.Message);
        }
    }

    private void Dispatch(string line)
    {
        JsonDocument doc;
        try
        {
            doc = JsonDocument.Parse(line);
        }
        catch (JsonException)
        {
            LogNotProtocol(line.Length > 200 ? line[..200] : line);
            return;
        }

        using (doc)
        {
            var root = doc.RootElement;
            if (root.TryGetProperty("event", out var ev) && ev.GetString() == "ready")
            {
                Hello = root.Deserialize<EngineHello>(FeedJson.Options);
                ready.TrySetResult();
                return;
            }

            if (!root.TryGetProperty("id", out var idElement) || idElement.ValueKind != JsonValueKind.Number ||
                !pending.TryGetValue(idElement.GetInt64(), out var reply))
            {
                return;                                   // an answer to a request that already timed out
            }

            if (root.TryGetProperty("ok", out var ok) && ok.GetBoolean())
            {
                reply.TrySetResult(root.TryGetProperty("result", out var result) ? result.Clone() : default);
                return;
            }

            var error = root.GetProperty("error");
            reply.TrySetException(new EngineException(
                error.TryGetProperty("type", out var type) ? type.GetString() ?? "EngineError" : "EngineError",
                error.TryGetProperty("message", out var message) ? message.GetString() ?? "" : ""));
        }
    }

    private void OnExited(Process p)
    {
        if (!ReferenceEquals(p, process))
        {
            return;                                       // an older process we already replaced
        }

        string tail;
        lock (tailLock)
        {
            tail = string.Join(Environment.NewLine, stderrTail.TakeLast(6));
        }

        var code = p.ExitCode;
        var why = $"The engine exited (code {code}).";
        LogExited(code, tail);
        ready.TrySetException(new EngineUnavailableException(why + (tail.Length > 0 ? " " + tail : "")));
        foreach (var (_, reply) in pending)
        {
            reply.TrySetException(new EngineUnavailableException(why));
        }

        if (Volatile.Read(ref disposed) == 0 && State != EngineConnectionState.Stopped)
        {
            SetState(EngineConnectionState.Restarting, why);
        }
    }

    private async Task StopProcessAsync()
    {
        var p = process;
        var j = job;
        process = null;
        job = null;
        if (p is null)
        {
            return;
        }

        try
        {
            if (!p.HasExited)
            {
                await p.StandardInput.WriteLineAsync("{\"id\":0,\"method\":\"shutdown\"}").ConfigureAwait(false);
                await p.StandardInput.FlushAsync().ConfigureAwait(false);
                using var grace = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                await p.WaitForExitAsync(grace.Token).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is IOException or OperationCanceledException or InvalidOperationException)
        {
            // It did not leave politely; the job object ends it below.
        }
        finally
        {
            j?.Terminate();
            j?.Dispose();
            p.Dispose();
        }
    }

    private void OnSettingsChanged(object? sender, AppSettings next)
    {
        var current = locator.Locate(next.EngineRoot, appDirectory);
        var running = Hello?.Root;
        if (running is not null && current.Engine?.Root is { } root &&
            string.Equals(Path.GetFullPath(root), Path.GetFullPath(running), StringComparison.OrdinalIgnoreCase))
        {
            return;
        }

        _ = RestartAsync(CancellationToken.None);          // the next call starts the engine from the new folder
    }

    private void SetState(EngineConnectionState state, string? error)
    {
        LastError = error;
        if (State == state)
        {
            return;
        }

        State = state;
        StateChanged?.Invoke(this, state);
    }

    private static TaskCompletionSource NewReady() => new(TaskCreationOptions.RunContinuationsAsynchronously);

    private sealed record Request(long Id, string Method, IReadOnlyDictionary<string, object?>? Params);

    [LoggerMessage(Level = LogLevel.Information, Message = "Engine server starting in {Root} (process {Pid})")]
    private partial void LogStarting(string root, int pid);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Engine server exited with code {Code}. Last output: {Tail}")]
    private partial void LogExited(int code, string tail);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Starting the engine server again (attempt {Attempt}): {Reason}")]
    private partial void LogRestarting(int attempt, string? reason);

    [LoggerMessage(Level = LogLevel.Debug, Message = "engine: {Line}")]
    private partial void LogEngineLine(string line);

    [LoggerMessage(Level = LogLevel.Warning, Message = "Ignored a line from the engine that is not protocol: {Line}")]
    private partial void LogNotProtocol(string line);

    [LoggerMessage(Level = LogLevel.Debug, Message = "Engine stream closed: {Reason}")]
    private partial void LogStreamClosed(string reason);
}
