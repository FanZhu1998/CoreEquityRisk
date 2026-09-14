using System.Diagnostics;
using System.Globalization;
using System.Management;
using EQRisk.Core.Engine;
using EQRisk.Core.Keys;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Engine;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Tests;

/// <summary>A folder under %TEMP% that is removed afterwards.</summary>
internal sealed class TempDir : IDisposable
{
    public TempDir()
    {
        Path = System.IO.Path.Combine(System.IO.Path.GetTempPath(), "eqrisk-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Path);
    }

    public string Path { get; }

    public string Combine(params string[] parts) => System.IO.Path.Combine([Path, .. parts]);

    public void Dispose()
    {
        for (var attempt = 0; attempt < 10; attempt++)
        {
            try
            {
                if (Directory.Exists(Path))
                {
                    Directory.Delete(Path, recursive: true);
                }

                return;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                Thread.Sleep(200);
            }
        }
    }
}

internal sealed class FixedSettings(AppSettings settings) : ISettingsStore
{
    public AppSettings Current { get; private set; } = settings;

    public event EventHandler<AppSettings>? Changed;

    public Task SaveAsync(AppSettings next, CancellationToken ct = default)
    {
        Current = next;
        Changed?.Invoke(this, next);
        return Task.CompletedTask;
    }
}

internal sealed class FakeLocator(EngineCheck check) : IEngineLocator
{
    public EngineCheck Check(string root) => check;

    public EngineCheck Locate(string? configuredRoot, string appDirectory) => check;
}

internal sealed class MemoryVault : IKeyVault
{
    private readonly Dictionary<string, string> values = new(StringComparer.Ordinal);

    public IReadOnlySet<string> Names => values.Keys.ToHashSet();

    public void Store(string name, string value) => values[name] = value;

    public bool Remove(string name) => values.Remove(name);

    public IReadOnlyDictionary<string, string> ReadForProcess(IEnumerable<string> names) =>
        names.Where(values.ContainsKey).ToDictionary(n => n, n => values[n]);
}

internal sealed class FakeProbe(params ExternalEngineRun[] runs) : IEngineProcessProbe
{
    public IReadOnlyList<ExternalEngineRun> FindJobs(IReadOnlyCollection<int> ownProcessIds) => runs;
}

/// <summary>Collects every formatted log message, to prove what is (and is not) logged.</summary>
internal sealed class ListLogger<T> : ILogger<T>
{
    private readonly Lock gate = new();
    private readonly List<string> messages = [];

    public IReadOnlyList<string> Messages
    {
        get
        {
            lock (gate)
            {
                return [.. messages];
            }
        }
    }

    public IDisposable? BeginScope<TState>(TState state)
        where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception,
        Func<TState, Exception?, string> formatter)
    {
        lock (gate)
        {
            messages.Add(formatter(state, exception));
        }
    }
}

/// <summary>The CoreEquityRisk checkout these tests run inside, when its engine is built.</summary>
internal static class RepoEngine
{
    public static EngineInfo? Find() => new EngineLocator().Locate(null, AppContext.BaseDirectory).Engine;

    public static bool HasModelData(EngineInfo engine) =>
        Directory.Exists(Path.Combine(engine.Root, "data", "model"));

    public static string? PyProject()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "pyproject.toml");
            if (File.Exists(candidate) && File.ReadAllText(candidate).Contains("name = \"eqrisk\"", StringComparison.Ordinal))
            {
                return candidate;
            }
        }

        return null;
    }
}

/// <summary>Stand-in commands for jobs, run by cmd.exe.</summary>
internal static class Cmd
{
    public static ProcessStartInfo Run(string script)
    {
        var psi = new ProcessStartInfo("cmd.exe")
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        psi.ArgumentList.Add("/d");
        psi.ArgumentList.Add("/c");
        psi.ArgumentList.Add(script);
        return psi;
    }
}

internal static class Processes
{
    /// <summary>A child of <paramref name="parentId"/> with the given image name, once it has started.</summary>
    public static Process WaitForChild(int parentId, string name)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (DateTime.UtcNow < deadline)
        {
            using var searcher = new ManagementObjectSearcher(
                $"SELECT ProcessId FROM Win32_Process WHERE ParentProcessId = {parentId} AND Name = '{name}'");
            using var results = searcher.Get();
            foreach (var item in results)
            {
                using (item)
                {
                    try
                    {
                        return Process.GetProcessById(Convert.ToInt32(item["ProcessId"], CultureInfo.InvariantCulture));
                    }
                    catch (ArgumentException)
                    {
                        // Already gone.
                    }
                }
            }

            Thread.Sleep(100);
        }

        throw new TimeoutException($"{name} never started under process {parentId}");
    }
}
