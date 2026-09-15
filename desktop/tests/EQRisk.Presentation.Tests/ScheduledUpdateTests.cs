using EQRisk.Core.Jobs;
using EQRisk.Presentation.Activity;
using Microsoft.Extensions.Logging.Abstractions;

namespace EQRisk.Presentation.Tests;

/// <summary>EQRisk.exe --run-daily, as Task Scheduler starts it: the exit code is what Task Scheduler shows.</summary>
public sealed class ScheduledUpdateTests
{
    private static ScheduledUpdate Run(FakeRunner runner) => new(runner, NullLogger<ScheduledUpdate>.Instance);

    [Fact]
    public async Task A_successful_update_exits_zero_and_is_recorded_as_scheduled()
    {
        var runner = new FakeRunner { Current = Sample.Job(JobKind.DailyUpdate, JobStatus.Succeeded) };
        Assert.Equal(ScheduledUpdate.Succeeded, await Run(runner).RunAsync(CancellationToken.None));
        var (spec, trigger) = runner.Started.Single();
        Assert.Equal("eqrisk run-daily", spec.CommandLine);
        Assert.Equal(JobTrigger.Schedule, trigger);
    }

    [Fact]
    public async Task A_failed_update_exits_one()
    {
        var runner = new FakeRunner { Current = Sample.Job(JobKind.DailyUpdate, JobStatus.Failed) };
        Assert.Equal(ScheduledUpdate.UpdateFailed, await Run(runner).RunAsync(CancellationToken.None));
    }

    [Fact]
    public async Task An_update_already_running_is_nothing_to_do()
    {
        var runner = new FakeRunner { StartFailure = new JobBusyException("Daily update is running.") };
        Assert.Equal(ScheduledUpdate.Succeeded, await Run(runner).RunAsync(CancellationToken.None));
        Assert.Empty(runner.Started);
    }

    [Fact]
    public async Task No_engine_folder_exits_two()
    {
        var runner = new FakeRunner { StartFailure = new InvalidOperationException("Choose the CoreEquityRisk folder in Settings.") };
        Assert.Equal(ScheduledUpdate.CouldNotStart, await Run(runner).RunAsync(CancellationToken.None));
    }
}
