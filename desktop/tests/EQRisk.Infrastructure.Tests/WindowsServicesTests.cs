using EQRisk.Infrastructure.Windows;
using Microsoft.Extensions.Logging.Abstractions;

namespace EQRisk.Infrastructure.Tests;

public sealed class WmiEngineProcessProbeTests
{
    [Theory]
    [InlineData(@"""C:\Users\me\CoreEquityRisk\.venv\Scripts\python.exe"" -m eqrisk.cli run-daily", "run-daily")]
    [InlineData(@"C:\Users\me\CoreEquityRisk\.venv\Scripts\eqrisk.exe backfill --stage model", "backfill")]
    [InlineData(@"python.exe -m eqrisk.cli validate --start 2019-01-02 --end 2026-09-11", "validate")]
    [InlineData(@"""C:\x\eqrisk.exe"" export-site", "export-site")]
    public void Engine_jobs_are_recognised(string commandLine, string command) =>
        Assert.Equal(command, WmiEngineProcessProbe.JobCommand(commandLine));

    [Theory]
    [InlineData(@"python.exe -m eqrisk.cli serve --root C:\x")]           // the app's own read server
    [InlineData(@"python.exe -m eqrisk.cli ui --port 8501")]              // the workbench
    [InlineData(@"python.exe -m pytest tests")]
    [InlineData(@"python.exe C:\Users\me\eqrisk_notes.py run-daily")]
    [InlineData(@"C:\Users\me\CoreEquityRisk\.venv\Scripts\python.exe -c ""print(1)""")]
    public void Other_python_processes_are_not_jobs(string commandLine) =>
        Assert.Null(WmiEngineProcessProbe.JobCommand(commandLine));

    [Fact]
    public void The_live_query_answers_without_throwing()
    {
        var runs = new WmiEngineProcessProbe(NullLogger<WmiEngineProcessProbe>.Instance).FindJobs([]);
        Assert.NotNull(runs);
    }
}

public sealed class ScheduledTaskServiceTests
{
    [Theory]
    [InlineData(1, "Disabled")]
    [InlineData(3, "Ready")]
    [InlineData(4, "Running")]
    [InlineData(0, "Unknown")]
    public void Task_states_have_names(int state, string name) => Assert.Equal(name, ScheduledTaskService.StateName(state));

    [Fact]
    public async Task A_task_that_does_not_exist_reads_as_null()
    {
        var service = new ScheduledTaskService(NullLogger<ScheduledTaskService>.Instance, $"EQRisk test {Guid.NewGuid():N}");
        Assert.Null(await service.GetAsync());
    }

    /// <summary>Creates and removes a throwaway task in Task Scheduler; run on purpose, not by default.</summary>
    [Fact(Explicit = true)]
    public async Task A_task_can_be_registered_read_and_removed()
    {
        var service = new ScheduledTaskService(NullLogger<ScheduledTaskService>.Instance, $"EQRisk test {Guid.NewGuid():N}");
        try
        {
            await service.RegisterAsync(new TimeOnly(6, 30), @"C:\Windows\System32\cmd.exe", "/c exit 0", @"C:\Windows");
            var info = await service.GetAsync();
            Assert.NotNull(info);
            Assert.Equal("Ready", info.State);
            Assert.Contains("cmd.exe", info.Command, StringComparison.OrdinalIgnoreCase);
            Assert.Equal(new TimeOnly(6, 30), TimeOnly.FromDateTime(info.NextRun!.Value.LocalDateTime));
        }
        finally
        {
            await service.RemoveAsync();
        }

        Assert.Null(await service.GetAsync());
    }
}
