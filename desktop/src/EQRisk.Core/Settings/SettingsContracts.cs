namespace EQRisk.Core.Settings;

public interface ISettingsStore
{
    AppSettings Current { get; }

    event EventHandler<AppSettings>? Changed;

    Task SaveAsync(AppSettings settings, CancellationToken ct = default);
}

/// <summary>The Windows scheduled task that runs the daily update, as Task Scheduler reports it.</summary>
public sealed record ScheduledTaskInfo(
    string State, DateTimeOffset? NextRun, DateTimeOffset? LastRun, int? LastResult, string? Command)
{
    public bool IsRunning => State.Equals("Running", StringComparison.OrdinalIgnoreCase);

    /// <summary>Task Scheduler reports 0 for success and 267011 (0x41303) for a task that has not run.</summary>
    public bool LastRunSucceeded => LastResult == 0;
}

public interface IScheduledTaskService
{
    string TaskName { get; }

    Task<ScheduledTaskInfo?> GetAsync(CancellationToken ct = default);

    /// <summary>Create or replace the daily task: run <paramref name="exePath"/> with
    /// <paramref name="arguments"/> every day at <paramref name="at"/>, and as soon as possible after a
    /// missed start.</summary>
    Task RegisterAsync(TimeOnly at, string exePath, string arguments, string workingDirectory,
        CancellationToken ct = default);

    Task RunNowAsync(CancellationToken ct = default);

    Task RemoveAsync(CancellationToken ct = default);
}
