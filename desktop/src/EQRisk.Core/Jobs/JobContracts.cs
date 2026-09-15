namespace EQRisk.Core.Jobs;

/// <summary>New log text from the running job.</summary>
public sealed record JobOutput(long JobId, string Text);

/// <summary>Another job is already running (this app's, or an eqrisk process started elsewhere).</summary>
public sealed class JobBusyException : Exception
{
    public JobBusyException()
    {
    }

    public JobBusyException(string message)
        : base(message)
    {
    }

    public JobBusyException(string message, Exception innerException)
        : base(message, innerException)
    {
    }
}

/// <summary>
/// Runs <c>eqrisk</c> commands as child processes, one at a time. Each process belongs to a Windows job
/// object, so it and anything it starts end when the app ends. Only jobs whose spec needs keys get
/// them, as environment variables.
/// </summary>
public interface IJobRunner
{
    /// <summary>The running job, or null.</summary>
    JobRecord? Current { get; }

    /// <summary>Process ids of the running job's tree root, for the external-run probe to ignore.</summary>
    IReadOnlyCollection<int> OwnProcessIds { get; }

    /// <summary>Raised when a job starts and when it ends.</summary>
    event EventHandler<JobRecord>? JobChanged;

    /// <summary>Raised with each chunk of the running job's output.</summary>
    event EventHandler<JobOutput>? Output;

    /// <summary>Start a job. Throws <see cref="JobBusyException"/> when one is already running.</summary>
    Task<JobRecord> StartAsync(JobSpec spec, JobTrigger trigger, CancellationToken ct = default);

    /// <summary>Wait for the running job, if any, to end.</summary>
    Task<JobRecord?> WaitAsync(CancellationToken ct = default);

    /// <summary>Stop the running job and everything it started.</summary>
    Task StopAsync();

    /// <summary>The tail of a job's log.</summary>
    Task<string> ReadLogAsync(JobRecord job, int maxChars, CancellationToken ct = default);
}

/// <summary>The job history, kept in SQLite.</summary>
public interface IJobStore
{
    Task<JobRecord> AddAsync(JobRecord record, CancellationToken ct = default);

    Task UpdateAsync(JobRecord record, CancellationToken ct = default);

    Task<JobRecord?> GetAsync(long id, CancellationToken ct = default);

    Task<IReadOnlyList<JobRecord>> RecentAsync(int limit, CancellationToken ct = default);

    /// <summary>Jobs still marked running from a previous session become interrupted. Returns how many.</summary>
    Task<int> MarkInterruptedAsync(DateTimeOffset now, CancellationToken ct = default);
}
