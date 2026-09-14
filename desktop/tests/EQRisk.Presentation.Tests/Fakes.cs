using EQRisk.Core.Engine;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Core.Settings;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Tests;

// Stand-ins for everything a view model talks to, so each can be tested with no window, engine or disk.

internal sealed class FakeFeed : IEngineFeed
{
    public ModelStatus? Status { get; set; }

    public Inventory Inventory { get; set; } = Sample.Inventory(new DateOnly(2026, 9, 11));

    public DayInfo? Day { get; set; }

    public HistoryInfo? History { get; set; }

    public IReadOnlyList<KeyPresence> Keys { get; set; } = [];

    public Exception? Failure { get; set; }

    public int Refreshes { get; private set; }

    public Task<ModelStatus> StatusAsync(CancellationToken ct = default) => Answer(Status);

    public Task<IReadOnlyList<KeyPresence>> KeysAsync(CancellationToken ct = default) => Answer(Keys);

    public Task<ModelDates> DatesAsync(CancellationToken ct = default) => throw new NotSupportedException();

    public Task<DayInfo> DayAsync(DateOnly? session = null, CancellationToken ct = default) => Answer(Day);

    public Task<HistoryInfo> HistoryAsync(int years, DateOnly? asOf = null, CancellationToken ct = default) => Answer(History);

    public Task<FactorRiskInfo> FactorRiskAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        throw new NotSupportedException();

    public Task<ExposuresInfo> ExposuresAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        throw new NotSupportedException();

    public Task<SpecificInfo> SpecificAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        throw new NotSupportedException();

    public Task<ValidationReport?> ValidationAsync(CancellationToken ct = default) => Task.FromResult<ValidationReport?>(null);

    public Task<Inventory> InventoryAsync(CancellationToken ct = default) => Answer(Inventory);

    public Task<ExceptionsInfo> ExceptionsAsync(int limit, CancellationToken ct = default) =>
        Task.FromResult(new ExceptionsInfo(0, [], []));

    public Task<RunsInfo> RunsAsync(int limit, CancellationToken ct = default) => Task.FromResult(new RunsInfo(0, []));

    public Task<EngineConfig> ConfigAsync(CancellationToken ct = default) => throw new NotSupportedException();

    public Task<OutputsInfo> OutputsAsync(CancellationToken ct = default) => throw new NotSupportedException();

    public Task<PortfolioReport> PortfolioAsync(IReadOnlyList<Holding> holdings, DateOnly? asOf = null,
        CancellationToken ct = default) => throw new NotSupportedException();

    public Task<OptimizeOutcome> OptimizeAsync(OptimizeRequest request, DateOnly? asOf = null, CancellationToken ct = default) =>
        throw new NotSupportedException();

    public Task RefreshAsync(CancellationToken ct = default)
    {
        Refreshes++;
        return Task.CompletedTask;
    }

    private Task<T> Answer<T>(T? value)
    {
        if (Failure is { } f)
        {
            return Task.FromException<T>(f);
        }

        return value is null ? Task.FromException<T>(new EngineException("EmptyResult", "not set in the test")) : Task.FromResult(value);
    }
}

internal sealed class FakeRunner : IJobRunner
{
    public List<(JobSpec Spec, JobTrigger Trigger)> Started { get; } = [];

    public Exception? StartFailure { get; set; }

    public JobRecord? Current { get; set; }

    public IReadOnlyCollection<int> OwnProcessIds => [];

    public event EventHandler<JobRecord>? JobChanged;

    public event EventHandler<JobOutput>? Output;

    public Task<JobRecord> StartAsync(JobSpec spec, JobTrigger trigger, CancellationToken ct = default)
    {
        if (StartFailure is { } f)
        {
            return Task.FromException<JobRecord>(f);
        }

        Started.Add((spec, trigger));
        var record = Sample.Job(spec.Kind, JobStatus.Running);
        return Task.FromResult(record);
    }

    public Task<JobRecord?> WaitAsync(CancellationToken ct = default) => Task.FromResult(Current);

    public Task StopAsync() => Task.CompletedTask;

    public Task<string> ReadLogAsync(JobRecord job, int maxChars, CancellationToken ct = default) => Task.FromResult("");

    public void Raise(JobRecord record) => JobChanged?.Invoke(this, record);

    public void Say(long jobId, string text) => Output?.Invoke(this, new JobOutput(jobId, text));
}

internal sealed class FakeVault : IKeyVault
{
    private readonly Dictionary<string, string> values = new(StringComparer.Ordinal);

    public IReadOnlySet<string> Names => values.Keys.ToHashSet();

    public void Store(string name, string value) => values[name] = value;

    public bool Remove(string name) => values.Remove(name);

    public IReadOnlyDictionary<string, string> ReadForProcess(IEnumerable<string> names) =>
        names.Where(values.ContainsKey).ToDictionary(n => n, n => values[n]);
}

internal sealed class FakeDialogs : IDialogs
{
    public List<string> Errors { get; } = [];

    public bool Answer { get; set; } = true;

    public Task<bool> ConfirmAsync(string title, string message, string confirm, ConfirmTone tone = ConfirmTone.Normal) =>
        Task.FromResult(Answer);

    public Task ShowErrorAsync(string title, string message)
    {
        Errors.Add($"{title}: {message}");
        return Task.CompletedTask;
    }

    public Task<string?> AskSecretAsync(string title, string message) => Task.FromResult<string?>(null);

    public string? PickFolder(string title, string? initialFolder) => null;

    public string? PickFile(string title, string filter, string? initialFolder) => null;

    public string? PickSaveFile(string title, string filter, string fileName, string? initialFolder) => null;
}

internal sealed class ImmediateDispatcher : IUiDispatcher
{
    public void Post(Action action) => action();
}

internal sealed class FakeNotifier : INotifier
{
    public List<(string Title, string Message, NoticeKind Kind)> Sent { get; } = [];

    public void Notify(string title, string message, NoticeKind kind) => Sent.Add((title, message, kind));
}

internal sealed class FakeSettings : ISettingsStore
{
    public AppSettings Current { get; private set; } = new();

    public event EventHandler<AppSettings>? Changed;

    public Task SaveAsync(AppSettings settings, CancellationToken ct = default)
    {
        Current = settings;
        Changed?.Invoke(this, settings);
        return Task.CompletedTask;
    }
}

internal sealed class FakeSchedule : IScheduledTaskService
{
    public ScheduledTaskInfo? Task { get; set; }

    public string TaskName => "EQRisk Daily";

    public Task<ScheduledTaskInfo?> GetAsync(CancellationToken ct = default) => System.Threading.Tasks.Task.FromResult(Task);

    public Task RegisterAsync(TimeOnly at, string exePath, string arguments, string workingDirectory,
        CancellationToken ct = default) => System.Threading.Tasks.Task.CompletedTask;

    public Task RunNowAsync(CancellationToken ct = default) => System.Threading.Tasks.Task.CompletedTask;

    public Task RemoveAsync(CancellationToken ct = default) => System.Threading.Tasks.Task.CompletedTask;
}

/// <summary>Engine answers shaped like the real ones.</summary>
internal static class Sample
{
    public static readonly DateOnly Last = new(2026, 9, 11);

    public static ModelStatus Status(IReadOnlyList<DateOnly>? pending = null, RunInfo? run = null) => new(
        "us_lc_v1", "0.1.0", "2026-09-14T19:02", Last, pending is { Count: > 0 } p ? p[^1] : Last, pending ?? [],
        new DateOnly(2026, 9, 14), "18:30", Last, 1, run,
        [new KeyPresence("EODHD_API_KEY", true), new KeyPresence("FRED_API_KEY", true), new KeyPresence("SEC_USER_AGENT", true)]);

    public static RunInfo Run(string status, params string[] failGates) => new(
        "r1", "run-daily --date 2026-09-11", Last, status, DateTimeOffset.Parse("2026-09-12T03:07:00+00:00", System.Globalization.CultureInfo.InvariantCulture),
        DateTimeOffset.Parse("2026-09-12T03:12:06+00:00", System.Globalization.CultureInfo.InvariantCulture), 5.1,
        [new GateResult("data_freshness", "FAIL", failGates.Length == 0, "503/503 coverage names price")], failGates, [], "hash", "sha", false);

    public static DayInfo Day() => new(Last, 0.0089, 0.28, 498, 20.3, "ok", 1.10, 1.08,
    [
        new FactorMove("COUNTRY", "country", 0.0089, 2.1, 0.9),
        new FactorMove("MOMENTUM", "style", 0.004, 1.5, 1.2),
        new FactorMove("SIZE", "style", -0.002, -1.1, -0.6),
        new FactorMove("BETA", "style", null, null, null),
        new FactorMove("ENERGY", "industry", 0.012, 1.9, 1.4),
        new FactorMove("BANKS", "industry", -0.008, -1.3, -0.9),
    ]);

    public static HistoryInfo History() => new(Last, 1, [Last], new Dictionary<string, IReadOnlyList<double?>>(), [Last],
        new Dictionary<string, IReadOnlyList<double?>>(), [Last], [1.1], [1.08], [0.01], [Last], [0.28]);

    public static Inventory Inventory(DateOnly eodLast) =>
        new(new DateOnly(2016, 1, 4), eodLast, 2691, 1156, [], Last, new Dictionary<string, System.Text.Json.JsonElement>(), "C:\\data", 600_000_000);

    public static JobRecord Job(JobKind kind, JobStatus status, TimeSpan? took = null)
    {
        var start = DateTimeOffset.UtcNow.AddMinutes(-5);
        return new JobRecord(7, kind, kind.ToString(), "run-daily", status, JobTrigger.App, start,
            status == JobStatus.Running ? null : start + (took ?? TimeSpan.FromSeconds(303)), status == JobStatus.Running ? null : 0,
            "x.log");
    }
}
