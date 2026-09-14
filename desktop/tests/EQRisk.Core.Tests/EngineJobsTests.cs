using EQRisk.Core.Jobs;

namespace EQRisk.Core.Tests;

/// <summary>Every command the app can start, against the flags of eqrisk/cli.py.</summary>
public sealed class EngineJobsTests
{
    private static readonly DateOnly Day = new(2026, 9, 11);

    [Fact]
    public void The_daily_update_downloads_so_it_needs_keys()
    {
        var spec = EngineJobs.DailyUpdate();
        Assert.Equal("eqrisk run-daily", spec.CommandLine);
        Assert.Equal(JobKind.DailyUpdate, spec.Kind);
        Assert.True(spec.NeedsKeys);
    }

    [Fact]
    public void The_stored_data_update_is_offline_and_needs_no_keys()
    {
        var spec = EngineJobs.DailyUpdateFromStoredData();
        Assert.Equal("eqrisk run-daily --offline", spec.CommandLine);
        Assert.False(spec.NeedsKeys);
    }

    [Theory]
    [InlineData(DataSource.FreshDownload, "eqrisk run-daily --date 2026-09-11 --force", true)]
    [InlineData(DataSource.StoredData, "eqrisk run-daily --date 2026-09-11 --force --offline", false)]
    [InlineData(DataSource.StoredStaging, "eqrisk run-daily --date 2026-09-11 --force --offline --no-stage", false)]
    public void A_reestimate_sets_the_flags_of_its_data_source(DataSource source, string expected, bool needsKeys)
    {
        var spec = EngineJobs.Reestimate(Day, source);
        Assert.Equal(expected, spec.CommandLine);
        Assert.Equal(needsKeys, spec.NeedsKeys);
    }

    [Fact]
    public void Command_lines_match_the_engine()
    {
        Assert.Equal("eqrisk ingest --date 2026-09-11", EngineJobs.DownloadSession(Day).CommandLine);
        Assert.Equal("eqrisk backfill --stage ingest --start 2025-09-11 --end 2026-09-11",
            EngineJobs.DownloadHistory(Day.AddYears(-1), Day).CommandLine);
        Assert.Equal("eqrisk backfill --stage stage --end 2026-09-11", EngineJobs.RebuildStaging(Day).CommandLine);
        Assert.Equal("eqrisk backfill --stage model --end 2026-09-11", EngineJobs.RebuildModel(Day).CommandLine);
        Assert.Equal("eqrisk validate --start 2019-01-02 --end 2026-09-11",
            EngineJobs.Validate(new DateOnly(2019, 1, 2), Day).CommandLine);
        Assert.Equal("eqrisk doctor --json", EngineJobs.TestConnections().CommandLine);
        Assert.Equal("eqrisk pull-overrides", EngineJobs.PullOverrides().CommandLine);
        Assert.Equal("eqrisk export-site", EngineJobs.ExportViewer().CommandLine);
        Assert.Equal("eqrisk export-site --date 2026-09-11", EngineJobs.ExportViewer(Day).CommandLine);
        Assert.Equal("eqrisk compact --month 2026-08", EngineJobs.CompactMonth(2026, 8).CommandLine);
    }

    [Fact]
    public void A_path_with_spaces_stays_one_argument()
    {
        var spec = EngineJobs.WriteSnapshot(Day, @"C:\My Data\snapshot 1.npz");
        Assert.Equal(["snapshot", "--date", "2026-09-11", "--out", @"C:\My Data\snapshot 1.npz"], spec.Arguments);
    }

    [Fact]
    public void Only_commands_that_call_a_vendor_receive_keys()
    {
        JobSpec[] vendor =
        [
            EngineJobs.DailyUpdate(), EngineJobs.DownloadSession(Day), EngineJobs.DownloadHistory(Day.AddDays(-9), Day),
            EngineJobs.PullOverrides(), EngineJobs.TestConnections(), EngineJobs.Reestimate(Day, DataSource.FreshDownload),
        ];
        JobSpec[] local =
        [
            EngineJobs.DailyUpdateFromStoredData(), EngineJobs.RebuildStaging(Day), EngineJobs.RebuildModel(Day),
            EngineJobs.Validate(Day.AddYears(-1), Day), EngineJobs.ExportViewer(), EngineJobs.WriteSnapshot(Day, "s.npz"),
            EngineJobs.CompactMonth(2026, 8), EngineJobs.Reestimate(Day, DataSource.StoredData),
            EngineJobs.Reestimate(Day, DataSource.StoredStaging),
        ];
        Assert.All(vendor, s => Assert.True(s.NeedsKeys, s.CommandLine));
        Assert.All(local, s => Assert.False(s.NeedsKeys, s.CommandLine));
    }

    [Fact]
    public void Reversed_windows_and_impossible_months_are_refused()
    {
        Assert.Throws<ArgumentException>(() => EngineJobs.Validate(Day, Day.AddDays(-1)));
        Assert.Throws<ArgumentException>(() => EngineJobs.DownloadHistory(Day, Day.AddDays(-1)));
        Assert.Throws<ArgumentOutOfRangeException>(() => EngineJobs.CompactMonth(2026, 0));
        Assert.Throws<ArgumentOutOfRangeException>(() => EngineJobs.CompactMonth(2026, 13));
        Assert.Throws<ArgumentException>(() => EngineJobs.WriteSnapshot(Day, " "));
    }
}
