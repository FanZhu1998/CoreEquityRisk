using EQRisk.Core.Jobs;
using Microsoft.Extensions.Logging;

namespace EQRisk.Presentation.Activity;

/// <summary>
/// What <c>EQRisk.exe --run-daily</c> does when Windows Task Scheduler starts it: run the daily update
/// with no window, record it in the job history like any other job, and exit with a code Task
/// Scheduler shows (0 success, 1 the update failed, 2 it could not start). If an update is already
/// running, from the open app or elsewhere, there is nothing to do.
/// </summary>
public sealed partial class ScheduledUpdate(IJobRunner runner, ILogger<ScheduledUpdate> log)
{
    public const int Succeeded = 0;
    public const int UpdateFailed = 1;
    public const int CouldNotStart = 2;

    public async Task<int> RunAsync(CancellationToken ct)
    {
        try
        {
            await runner.StartAsync(EngineJobs.DailyUpdate(), JobTrigger.Schedule, ct);
        }
        catch (JobBusyException ex)
        {
            LogBusy(ex.Message);
            return Succeeded;
        }
        catch (InvalidOperationException ex)
        {
            LogCannotStart(ex.Message);
            return CouldNotStart;
        }

        var done = await runner.WaitAsync(ct);
        LogDone(done?.Status ?? JobStatus.Interrupted);
        return done?.Status == JobStatus.Succeeded ? Succeeded : UpdateFailed;
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Scheduled update skipped: {Reason}")]
    private partial void LogBusy(string reason);

    [LoggerMessage(Level = LogLevel.Error, Message = "Scheduled update could not start: {Reason}")]
    private partial void LogCannotStart(string reason);

    [LoggerMessage(Level = LogLevel.Information, Message = "Scheduled update finished: {Status}")]
    private partial void LogDone(JobStatus status);
}
