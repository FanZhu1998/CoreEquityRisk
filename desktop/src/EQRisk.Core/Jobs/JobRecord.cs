namespace EQRisk.Core.Jobs;

/// <summary>The life of one engine job.</summary>
public enum JobStatus
{
    Running,
    Succeeded,
    Failed,
    Stopped,

    /// <summary>The app closed or crashed while the job ran; found as running at the next start.</summary>
    Interrupted,
}

/// <summary>Who started a job.</summary>
public enum JobTrigger
{
    App,
    Tray,
    Schedule,
}

/// <summary>One engine job as the history keeps it: what ran, how it ended, and where its log is.</summary>
public sealed record JobRecord(
    long Id,
    JobKind Kind,
    string Label,
    string Arguments,
    JobStatus Status,
    JobTrigger Trigger,
    DateTimeOffset StartedAt,
    DateTimeOffset? FinishedAt,
    int? ExitCode,
    string LogPath)
{
    public bool IsRunning => Status == JobStatus.Running;

    public TimeSpan? Duration => FinishedAt - StartedAt;

    public string CommandLine => "eqrisk " + Arguments;
}
