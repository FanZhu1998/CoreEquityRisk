using System.Globalization;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Pages;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Tests;

public sealed class FmtTests
{
    public FmtTests() => CultureInfo.CurrentCulture = CultureInfo.InvariantCulture;

    [Fact]
    public void Missing_values_read_as_a_dash_never_a_zero()
    {
        Assert.Equal(Fmt.Dash, Fmt.Pct(null));
        Assert.Equal(Fmt.Dash, Fmt.Pct(double.NaN));
        Assert.Equal(Fmt.Dash, Fmt.WithSign(double.PositiveInfinity));
        Assert.Equal(Fmt.Dash, Fmt.Date(null));
    }

    [Fact]
    public void Numbers_read_as_a_person_writes_them()
    {
        Assert.Equal("12.3%", Fmt.Pct(0.1234));
        Assert.Equal("-1.23%", Fmt.SignedPct(-0.0123));
        Assert.Equal("+0.89%", Fmt.SignedPct(0.0089));
        Assert.Equal("+1.50", Fmt.WithSign(1.5));
        Assert.Equal("600 MB", Fmt.Bytes(600_403_815));
        Assert.Equal("1.2 GB", Fmt.Bytes(1_234_000_000));
        Assert.Equal("5 min 03 s", Fmt.Duration(TimeSpan.FromSeconds(303)));
        Assert.Equal("2026-09-11", Fmt.Date(new DateOnly(2026, 9, 11)));
        Assert.Equal("1 session", Fmt.Plural(1, "session", "sessions"));
    }

    [Fact]
    public void Factor_codes_read_as_words() =>
        Assert.Equal("Consumer durables apparel", Fmt.Factor("CONSUMER_DURABLES_APPAREL"));
}

public sealed class HoldingsCsvTests
{
    [Fact]
    public void Tickers_weights_and_benchmark_weights_are_read()
    {
        var (holdings, problems) = HoldingsCsv.Parse("Ticker, Weight, bench_weight\r\n aapl ,0.6,0.5\n\"MSFT\",0.4,\n");
        Assert.Empty(problems);
        Assert.Equal(["AAPL", "MSFT"], holdings.Select(h => h.Ticker));
        Assert.Equal(0.5, holdings[0].BenchWeight);
        Assert.Null(holdings[1].BenchWeight);
    }

    [Fact]
    public void Bad_lines_are_skipped_and_reported()
    {
        var (holdings, problems) = HoldingsCsv.Parse("ticker,weight\nAAPL,abc\n,0.1\nMSFT,0.2\n");
        Assert.Single(holdings);
        Assert.Equal(2, problems.Count);
        Assert.Contains("Line 2", problems[0], StringComparison.Ordinal);
    }

    [Fact]
    public void A_file_without_the_columns_is_refused() =>
        Assert.Contains("ticker and weight", HoldingsCsv.Parse("symbol,w\nAAPL,1\n").Problems.Single(), StringComparison.Ordinal);
}

public sealed class DatesAndStatusTests
{
    private static readonly DateOnly[] Dates = [new(2026, 9, 8), new(2026, 9, 9), new(2026, 9, 11)];

    [Theory]
    [InlineData(2026, 9, 9, 2026, 9, 9)]      // a model date is kept
    [InlineData(2026, 9, 10, 2026, 9, 9)]     // a day without one snaps back
    [InlineData(2026, 9, 14, 2026, 9, 11)]    // after the last, the last
    [InlineData(2026, 1, 1, 2026, 9, 8)]      // before the first, the first
    public void An_as_of_date_snaps_to_the_model_date_on_or_before_it(int y, int m, int d, int ey, int em, int ed) =>
        Assert.Equal(new DateOnly(ey, em, ed), DatedPageViewModel.Snap(Dates, new DateOnly(y, m, d)));

    [Fact]
    public void A_quarantined_run_is_the_headline_even_with_sessions_pending()
    {
        var view = StatusSummary.From(Sample.Status([new DateOnly(2026, 9, 14)], Sample.Run("QUARANTINED", "factor_shock")));
        Assert.Equal(Health.Problem, view.Health);
        Assert.Contains("factor_shock", view.Detail, StringComparison.Ordinal);
    }

    [Fact]
    public void Pending_and_up_to_date_read_plainly()
    {
        Assert.Equal(Health.Pending, StatusSummary.From(Sample.Status([new DateOnly(2026, 9, 14)])).Health);
        var ok = StatusSummary.From(Sample.Status([], Sample.Run("OK")));
        Assert.Equal(Health.UpToDate, ok.Health);
        Assert.Equal("Up to date through 2026-09-11", ok.Headline);
    }
}

public sealed class JobLauncherTests
{
    private readonly FakeRunner runner = new();
    private readonly FakeFeed feed = new();
    private readonly FakeVault vault = new();
    private readonly FakeDialogs dialogs = new();

    private JobLauncher Launcher() => new(runner, feed, vault, dialogs);

    [Fact]
    public async Task A_vendor_job_does_not_start_without_its_keys()
    {
        feed.Keys = [new("EODHD_API_KEY", false), new("FRED_API_KEY", true), new("SEC_USER_AGENT", true)];
        Assert.False(await Launcher().RunAsync(EngineJobs.DailyUpdate()));
        Assert.Empty(runner.Started);
        Assert.Contains("EODHD_API_KEY", dialogs.Errors.Single(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task A_key_in_the_vault_is_as_good_as_one_in_env()
    {
        feed.Keys = [new("EODHD_API_KEY", false), new("FRED_API_KEY", true), new("SEC_USER_AGENT", true)];
        vault.Store("EODHD_API_KEY", "x");
        Assert.True(await Launcher().RunAsync(EngineJobs.DailyUpdate()));
        Assert.Single(runner.Started);
    }

    [Fact]
    public async Task A_local_job_never_asks_about_keys()
    {
        feed.Failure = new EngineUnavailableException("down");
        Assert.True(await Launcher().RunAsync(EngineJobs.RebuildStaging(Sample.Last)));
    }

    [Fact]
    public async Task A_busy_runner_becomes_a_message_not_a_crash()
    {
        runner.StartFailure = new JobBusyException("Daily update is running.");
        Assert.False(await Launcher().RunAsync(EngineJobs.Validate(Sample.Last.AddYears(-1), Sample.Last)));
        Assert.StartsWith("A job is already running", dialogs.Errors.Single(), StringComparison.Ordinal);
    }
}

public sealed class TodayViewModelTests
{
    private readonly FakeFeed feed = new() { Day = Sample.Day(), History = Sample.History() };
    private readonly FakeRunner runner = new();

    private TodayViewModel Page() =>
        new(feed, new JobLauncher(runner, feed, new FakeVault(), new FakeDialogs()), new FakeSchedule(), new WeakReferenceMessenger());

    [Fact]
    public async Task With_a_session_pending_the_update_button_is_offered()
    {
        feed.Status = Sample.Status([new DateOnly(2026, 9, 14)]);
        var page = Page();
        await page.ReloadAsync();

        Assert.Null(page.Problem);
        Assert.True(page.HasPending);
        Assert.Equal("Ready to estimate 1 session: 2026-09-14", page.ActionTitle);
        Assert.True(page.RunUpdateCommand.CanExecute(null));
        Assert.False(page.CanUseStoredData);                 // raw prices end 2026-09-11, before the pending session
    }

    [Fact]
    public async Task Up_to_date_offers_a_rerun_and_names_the_next_session()
    {
        feed.Status = Sample.Status([], Sample.Run("OK"));
        var page = Page();
        await page.ReloadAsync();

        Assert.False(page.HasPending);
        Assert.StartsWith("Up to date through 2026-09-11", page.ActionTitle, StringComparison.Ordinal);
        Assert.Contains("2026-09-14", page.ActionNote, StringComparison.Ordinal);
        Assert.True(page.RerunLastCommand.CanExecute(null));
        Assert.Equal("pass", page.Gates.Single().Result);
    }

    [Fact]
    public async Task Style_moves_are_the_styles_only_sorted_with_missing_ones_left_out()
    {
        feed.Status = Sample.Status();
        var page = Page();
        await page.ReloadAsync();

        Assert.Equal(["Size", "Momentum"], page.StyleMoves!.Bars.Select(b => b.Label));
        Assert.Equal([Tone.Bear, Tone.Bull], page.StyleMoves.Bars.Select(b => b.Tone));
        Assert.Contains("Energy +1.20%", page.IndustryLine, StringComparison.Ordinal);
    }

    [Fact]
    public async Task An_engine_that_is_down_shows_why_instead_of_throwing()
    {
        feed.Failure = new EngineUnavailableException("The engine exited (code 1).");
        var page = Page();
        await page.ReloadAsync();
        Assert.StartsWith("The engine is not running.", page.Problem, StringComparison.Ordinal);
    }
}

public sealed class JobActivityViewModelTests
{
    [Fact]
    public void A_daily_update_moves_through_its_steps_and_ends_with_its_outcome()
    {
        var runner = new FakeRunner();
        var feed = new FakeFeed();
        var messenger = new WeakReferenceMessenger();
        var finished = new List<JobRecord>();
        messenger.Register<JobFinishedMessage>(this, (_, m) => finished.Add(m.Job));
        var notifier = new FakeNotifier();
        using var bar = new JobActivityViewModel(runner, new ImmediateDispatcher(), messenger, feed, notifier, new FakeSettings(),
            TimeProvider.System);

        var running = Sample.Job(JobKind.DailyUpdate, JobStatus.Running);
        runner.Raise(running);
        Assert.True(bar.IsRunning);
        Assert.True(bar.ShowsSteps);
        Assert.True(bar.StopCommand.CanExecute(null));

        runner.Say(running.Id, "2026-09-14 ingested source=eodhd\nstaging through=2026-09-14\n");
        Assert.Equal(2, bar.Step);
        runner.Say(running.Id, "descriptors sessions=2691\n");
        Assert.Equal(3, bar.Step);
        Assert.Contains("descriptors", bar.LogTail, StringComparison.Ordinal);

        runner.Raise(running with { Status = JobStatus.Succeeded, FinishedAt = running.StartedAt.AddSeconds(303), ExitCode = 0 });
        Assert.False(bar.IsRunning);
        Assert.Equal("Finished in 5 min 03 s.", bar.Outcome);
        Assert.Equal(bar.StepNames.Count - 1, bar.Step);
        Assert.Equal(1, feed.Refreshes);                   // the engine drops its caches before pages re-read
        Assert.Single(finished);
        Assert.Equal(NoticeKind.Success, notifier.Sent.Single().Kind);
    }

    [Fact]
    public void A_failed_job_opens_its_log()
    {
        var runner = new FakeRunner();
        using var bar = new JobActivityViewModel(runner, new ImmediateDispatcher(), new WeakReferenceMessenger(), new FakeFeed(),
            new FakeNotifier(), new FakeSettings(), TimeProvider.System);
        var job = Sample.Job(JobKind.Validation, JobStatus.Running);
        runner.Raise(job);
        Assert.False(bar.ShowsSteps);
        runner.Raise(job with { Status = JobStatus.Failed, FinishedAt = job.StartedAt.AddSeconds(12), ExitCode = 1 });
        Assert.True(bar.Failed);
        Assert.True(bar.IsLogOpen);
        Assert.StartsWith("Failed after 12 s (exit code 1)", bar.Outcome, StringComparison.Ordinal);
    }
}
