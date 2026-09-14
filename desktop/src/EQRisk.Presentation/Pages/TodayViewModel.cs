using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Settings;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Charts;

namespace EQRisk.Presentation.Pages;

/// <summary>
/// Today: where the model stands and one button to bring it up to date, then that session's factor
/// moves, the quality gates of the last run and the volatility regime over the last year.
/// </summary>
public sealed partial class TodayViewModel : PageViewModel
{
    private const int IndustriesShown = 3;
    private const int SessionsListed = 5;

    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly IScheduledTaskService schedule;

    public TodayViewModel(IEngineFeed feed, JobLauncher launcher, IScheduledTaskService schedule, IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.launcher = launcher;
        this.schedule = schedule;
        Stats = [];
        Gates = [];
        ActionTitle = "";
        ActionNote = "";
        ScheduleLine = "";
        IndustryLine = "";
        GatesNote = "";
    }

    public override string Key => "today";

    public override string Title => "Today";

    public override string Eyebrow => "01 — Today";

    public override string Heading => "US large-cap";

    public override string HeadingAccent => "risk model";

    public override string? Lede =>
        "Daily factor estimation for the point-in-time S&P 500. Each update loads the latest session, restages the " +
        "data, re-estimates exposures, factor returns, the factor covariance and specific risk, and publishes it only " +
        "if the quality gates pass.";

    [ObservableProperty]
    public partial IReadOnlyList<Stat> Stats { get; set; }

    [ObservableProperty]
    public partial string ActionTitle { get; set; }

    [ObservableProperty]
    public partial string ActionNote { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(RunUpdateCommand), nameof(UseStoredDataCommand))]
    public partial bool HasPending { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(UseStoredDataCommand))]
    public partial bool CanUseStoredData { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(RerunLastCommand))]
    public partial DateOnly? LastSession { get; set; }

    /// <summary>Required keys set nowhere, as a sentence; null when every one is configured.</summary>
    [ObservableProperty]
    public partial string? MissingKeys { get; set; }

    [ObservableProperty]
    public partial string ScheduleLine { get; set; }

    [ObservableProperty]
    public partial BarChart? StyleMoves { get; set; }

    [ObservableProperty]
    public partial string IndustryLine { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<GateRow> Gates { get; set; }

    [ObservableProperty]
    public partial string GatesNote { get; set; }

    [ObservableProperty]
    public partial TimeChart? Regime { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var status = await feed.StatusAsync(ct);
        var inventory = await feed.InventoryAsync(ct);
        var day = status.Last is { } last ? await feed.DayAsync(last, ct) : null;
        var history = status.Last is not null ? await feed.HistoryAsync(1, ct: ct) : null;
        var missing = await launcher.MissingRequiredKeysAsync();

        LastSession = status.Last;
        HasPending = !status.UpToDate;
        CanUseStoredData = HasPending && inventory.EodLast is { } eod && eod >= status.Pending[^1];
        MissingKeys = missing is { Count: > 0 }
            ? $"Missing: {string.Join(", ", missing)}. Add them on the Data page or in the engine's .env file."
            : null;
        Stats = BuildStats(status, day);
        (ActionTitle, ActionNote) = Action(status);
        Gates = status.LastRun?.Gates.Select(g => new GateRow(Fmt.Factor(g.Gate), g.Ok ? "pass" : g.Level.ToLowerInvariant(),
            g.Ok ? Tone.Bull : g.Level == "FAIL" ? Tone.Bear : Tone.Warn, g.Detail)).ToList() ?? [];
        GatesNote = status.LastRun is { } run
            ? $"{run.Command} · finished {Fmt.Time(run.FinishedAt)}" + (run.Minutes is { } m ? $" · {m:0.0} min" : "")
            : "Gates are evaluated by daily updates; the history built by the backfill has none.";
        StyleMoves = day is null ? null : Moves(day);
        IndustryLine = day is null ? "" : Industries(day);
        Regime = history is null ? null : new TimeChart(
        [
            new DatedSeries("λF factor", history.VraDates, history.LambdaF, Tone.Accent),
            new DatedSeries("λS specific", history.VraDates, history.LambdaS, Tone.Warn),
        ], Guide: 1.0);
        ScheduleLine = await ScheduleAsync(ct);
    }

    protected override void OnBusyChanged()
    {
        RunUpdateCommand.NotifyCanExecuteChanged();
        UseStoredDataCommand.NotifyCanExecuteChanged();
        RerunLastCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand(CanExecute = nameof(CanRunUpdate))]
    private async Task RunUpdateAsync() => await launcher.RunAsync(EngineJobs.DailyUpdate());

    private bool CanRunUpdate() => HasPending && !launcher.IsBusy;

    [RelayCommand(CanExecute = nameof(CanRunStored))]
    private async Task UseStoredDataAsync() => await launcher.RunAsync(EngineJobs.DailyUpdateFromStoredData());

    private bool CanRunStored() => CanUseStoredData && !launcher.IsBusy;

    [RelayCommand(CanExecute = nameof(CanRerun))]
    private Task RerunLastAsync() =>
        LastSession is { } d ? launcher.RunAsync(EngineJobs.Reestimate(d, DataSource.StoredData)) : Task.CompletedTask;

    private bool CanRerun() => LastSession is not null && !launcher.IsBusy;

    private static List<Stat> BuildStats(ModelStatus status, DayInfo? day)
    {
        var run = status.LastRun;
        return
        [
            new("Model as of", Fmt.Date(status.Last)),
            new("Latest good (published)", Fmt.Date(status.LatestGood)),
            new("Last daily update", run is null ? "none yet" : $"{run.Status} · {Fmt.Date(run.AsOf)}",
                run is null ? Tone.Muted : Tones.Status(run.Status)),
            new("Market (country factor)", Fmt.SignedPct(day?.Country), Tones.Sign(day?.Country)),
            new("Regime λF / λS", day?.LambdaF is { } f && day.LambdaS is { } s
                ? $"{Fmt.Num(f)} / {Fmt.Num(s)}" : Fmt.Dash, Tone.Ink,
                "Above 1: recent risk has run higher than forecast, so forecasts are scaled up."),
            new("Cross-sectional R²", day?.R2 is { } r ? $"{Fmt.Pct(r)} · {Fmt.Count(day.N)} names" : Fmt.Dash),
        ];
    }

    private static (string Title, string Note) Action(ModelStatus status)
    {
        if (status.Last is null)
        {
            return ("No model yet.", "Build the history first: Data › Load data › Date range, then Estimate › Rebuild.");
        }

        if (!status.UpToDate)
        {
            var n = status.Pending.Count;
            var listed = string.Join(", ", status.Pending.Take(SessionsListed).Select(d => Fmt.Date(d))) + (n > SessionsListed ? " …" : "");
            return ($"Ready to estimate {Fmt.Plural(n, "session", "sessions")}: {listed}",
                "Loads prices, filings and reference data for each session, restages, re-estimates and runs the gates. " +
                "About five minutes a session.");
        }

        return ($"Up to date through {Fmt.Date(status.Last)}.",
            $"The next session, {Fmt.Date(status.NextSession)}, can be estimated after {status.ReadyAfterEt} ET that day, " +
            "once vendors publish end-of-day data. Re-running a session recomputes it from the raw data on disk.");
    }

    private static BarChart Moves(DayInfo day)
    {
        var bars = day.Moves
            .Where(m => m.Group == "style" && m.FOverSigma is not null)
            .OrderBy(m => m.FOverSigma)
            .Select(m => new Bar(Fmt.Factor(m.Factor), m.FOverSigma!.Value, Tones.Sign(m.FOverSigma)))
            .ToList();
        return new BarChart(bars, "+0.00;-0.00", Guide: 0);
    }

    private static string Industries(DayInfo day)
    {
        var inds = day.Moves.Where(m => m.Group == "industry" && m.F is not null).OrderBy(m => m.F).ToList();
        if (inds.Count == 0)
        {
            return "";
        }

        static string Line(IEnumerable<FactorMove> ms) =>
            string.Join(", ", ms.Select(m => $"{Fmt.Factor(m.Factor)} {Fmt.SignedPct(m.F)}"));

        return $"Industries — best: {Line(Enumerable.Reverse(inds).Take(IndustriesShown))} · " +
               $"worst: {Line(inds.Take(IndustriesShown))}";
    }

    private async Task<string> ScheduleAsync(CancellationToken ct)
    {
        try
        {
            var task = await schedule.GetAsync(ct);
            if (task is null)
            {
                return "Scheduled daily update: not registered. Set it up in Settings, or keep running updates from here.";
            }

            var result = task.LastResult is { } code && task.LastRun is not null
                ? code == 0 ? " · last result OK" : string.Create(CultureInfo.InvariantCulture, $" · last result 0x{code:X}")
                : "";
            return $"Scheduled daily update: {task.State.ToLowerInvariant()} · next run {Fmt.Time(task.NextRun)} · " +
                   $"last run {Fmt.Time(task.LastRun)}{result}";
        }
        catch (Exception ex) when (ex is System.Runtime.InteropServices.COMException or PlatformNotSupportedException
                                       or UnauthorizedAccessException)
        {
            return "Scheduled daily update: Task Scheduler could not be read.";
        }
    }
}
