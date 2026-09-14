using System.Diagnostics;
using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Jobs;
using EQRisk.Infrastructure.Storage;

namespace EQRisk.Infrastructure.Tests;

/// <summary>The job runner, with cmd.exe standing in for the engine.</summary>
public sealed class JobRunnerTests : IDisposable
{
    private const string VaultSecret = "vault-secret-3a9d";
    private static readonly DateOnly Day = new(2026, 9, 11);

    private readonly TempDir dir = new();
    private readonly MemoryVault vault = new();
    private readonly ListLogger<JobRunner> log = new();
    private readonly SqliteJobStore store;

    public JobRunnerTests() => store = new SqliteJobStore(new AppPaths(dir.Path));

    public void Dispose() => dir.Dispose();

    private JobRunner Runner(Func<EngineInfo, JobSpec, ProcessStartInfo> command, IEngineProcessProbe? probe = null,
        EngineCheck? engine = null) =>
        new(new FakeLocator(engine ?? new EngineCheck(new EngineInfo(dir.Path, "cmd.exe", "test"), null)),
            new FixedSettings(new AppSettings()), store, vault, probe ?? new FakeProbe(), new AppPaths(dir.Path),
            TimeProvider.System, log, dir.Path)
        {
            CommandBuilder = command,
        };

    [Fact]
    public async Task A_job_streams_its_output_to_listeners_the_log_and_the_history()
    {
        using var runner = Runner((_, _) => Cmd.Run("echo staging through 2026-09-11& echo all done"));
        var seen = new List<string>();
        runner.Output += (_, o) => seen.Add(o.Text);

        var job = await runner.StartAsync(EngineJobs.RebuildStaging(Day), JobTrigger.App);
        Assert.Equal(JobStatus.Running, job.Status);
        var done = await runner.WaitAsync();

        Assert.Equal(JobStatus.Succeeded, done!.Status);
        Assert.Equal(0, done.ExitCode);
        Assert.Contains(seen, t => t.Contains("staging through", StringComparison.Ordinal));
        var logText = await runner.ReadLogAsync(done, 10_000);
        Assert.Contains("# eqrisk backfill --stage stage --end 2026-09-11", logText, StringComparison.Ordinal);
        Assert.Contains("all done", logText, StringComparison.Ordinal);
        Assert.Contains("succeeded with exit code 0", logText, StringComparison.Ordinal);
        Assert.Equal(JobStatus.Succeeded, (await store.GetAsync(done.Id))!.Status);
        Assert.Null(runner.Current);
    }

    [Fact]
    public async Task A_failing_job_is_recorded_with_its_exit_code()
    {
        using var runner = Runner((_, _) => Cmd.Run("exit 3"));
        await runner.StartAsync(EngineJobs.Validate(Day.AddYears(-1), Day), JobTrigger.App);
        var done = await runner.WaitAsync();
        Assert.Equal(JobStatus.Failed, done!.Status);
        Assert.Equal(3, done.ExitCode);
    }

    [Fact]
    public async Task One_job_at_a_time_and_a_stop_ends_it()
    {
        using var runner = Runner((_, _) => Cmd.Run("ping -n 60 127.0.0.1 >nul"));
        await runner.StartAsync(EngineJobs.RebuildModel(Day), JobTrigger.App);

        await Assert.ThrowsAsync<JobBusyException>(() => runner.StartAsync(EngineJobs.RebuildStaging(Day), JobTrigger.App));

        await runner.StopAsync();
        var done = await runner.WaitAsync();
        Assert.Equal(JobStatus.Stopped, done!.Status);
        Assert.Null(runner.Current);
    }

    [Fact]
    public async Task Vault_keys_reach_only_jobs_that_call_a_vendor_and_are_never_logged()
    {
        vault.Store("EODHD_API_KEY", VaultSecret);
        using var runner = Runner((_, _) => Cmd.Run("echo key=%EODHD_API_KEY%"));

        await runner.StartAsync(EngineJobs.DownloadSession(Day), JobTrigger.App);      // calls the vendor
        var withKeys = await runner.ReadLogAsync((await runner.WaitAsync())!, 10_000);
        await runner.StartAsync(EngineJobs.RebuildStaging(Day), JobTrigger.App);        // does not
        var withoutKeys = await runner.ReadLogAsync((await runner.WaitAsync())!, 10_000);

        Assert.Contains($"key={VaultSecret}", withKeys, StringComparison.Ordinal);
        Assert.DoesNotContain(VaultSecret, withoutKeys, StringComparison.Ordinal);
        Assert.Contains(log.Messages, m => m.Contains("keys from the vault: EODHD_API_KEY", StringComparison.Ordinal));
        Assert.DoesNotContain(log.Messages, m => m.Contains(VaultSecret, StringComparison.Ordinal));
    }

    [Fact]
    public async Task A_job_started_elsewhere_blocks_a_new_one()
    {
        using var runner = Runner((_, _) => Cmd.Run("exit 0"),
            probe: new FakeProbe(new ExternalEngineRun(4242, "python -m eqrisk.cli run-daily", null)));
        var ex = await Assert.ThrowsAsync<JobBusyException>(() => runner.StartAsync(EngineJobs.DailyUpdate(), JobTrigger.App));
        Assert.Contains("outside the app", ex.Message, StringComparison.Ordinal);
        Assert.Empty(await store.RecentAsync(5));
    }

    [Fact]
    public async Task Without_an_engine_the_reason_is_given()
    {
        using var runner = Runner((_, _) => Cmd.Run("exit 0"), engine: EngineCheck.Fail("Choose the CoreEquityRisk folder in Settings."));
        var ex = await Assert.ThrowsAsync<InvalidOperationException>(() => runner.StartAsync(EngineJobs.DailyUpdate(), JobTrigger.App));
        Assert.Contains("Choose the CoreEquityRisk folder", ex.Message, StringComparison.Ordinal);
    }
}
