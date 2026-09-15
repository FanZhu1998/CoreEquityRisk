using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Pages;

/// <summary>
/// Validate: the strictly point-in-time backtest and its scorecard. Bias statistics near 1 mean the
/// risk forecast was right on average; MRAD says how steadily; each blueprint criterion is PASS or FAIL.
/// </summary>
public sealed partial class ValidateViewModel : PageViewModel
{
    private static readonly DateTime DefaultStart = new(2019, 1, 2);

    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly IShell shell;

    public ValidateViewModel(IEngineFeed feed, JobLauncher launcher, IShell shell, IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.launcher = launcher;
        this.shell = shell;
        Stats = [];
        Scorecard = [];
        External = [];
        Start = DefaultStart;
    }

    public override string Key => "validate";

    public override string Title => "Validate";

    public override string Eyebrow => "04 — Validate";

    public override string Heading => "Model";

    public override string HeadingAccent => "validation";

    public override string? Lede =>
        "A strictly point-in-time backtest: every forecast made at a close is scored against the next 21 sessions, on " +
        "non-overlapping periods. Bias statistics near 1 mean the risk was right on average, MRAD measures how steadily, " +
        "and the scorecard checks each success criterion of the blueprint (§1.3).";

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(RunCommand))]
    public partial DateTime? Start { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(RunCommand))]
    public partial DateTime? End { get; set; }

    [ObservableProperty]
    public partial bool HasReport { get; set; }

    [ObservableProperty]
    public partial string? ReportFolder { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<Stat> Stats { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<ScoreLine> Scorecard { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<ScoreLine> External { get; set; }

    [ObservableProperty]
    public partial BiasChart? FactorBias { get; set; }

    [ObservableProperty]
    public partial XyChart? Eigen { get; set; }

    [ObservableProperty]
    public partial XyChart? Deciles { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var status = await feed.StatusAsync(ct);
        End ??= status.Last?.ToDateTime(TimeOnly.MinValue);
        var report = await feed.ValidationAsync(ct);
        HasReport = report is not null;
        if (report is null)
        {
            return;
        }

        ReportFolder = report.Dir;
        var pass = report.Scorecard.Count(r => r.Status == "PASS");
        var fail = report.Scorecard.Count(r => r.Status == "FAIL");
        Stats =
        [
            new("Window", $"{Fmt.Date(report.Start)} → {Fmt.Date(report.End)}"),
            new("Forecast periods", Fmt.Count(report.Periods)),
            new("Criteria passed", $"{pass} of {report.Scorecard.Count}", Tone.Bull),
            new("Criteria failed", Fmt.Count(fail), fail > 0 ? Tone.Bear : Tone.Bull),
            new("Not yet measurable", Fmt.Count(report.Scorecard.Count - pass - fail), Tone.Muted),
        ];
        Scorecard = report.Scorecard.Select(Line).ToList();
        External = report.External.Select(Line).ToList();
        FactorBias = Bias(report);
        Eigen = new XyChart(
        [
            new XySeries("Before the adjustment", report.Eigen.Select(e => e.K + 1).ToList(), report.Eigen.Select(e => e.BiasBefore).ToList(), Tone.Muted),
            new XySeries("After the adjustment", report.Eigen.Select(e => e.K + 1).ToList(), report.Eigen.Select(e => e.BiasAfter).ToList(), Tone.Accent),
        ], "Eigenfactor, smallest variance first", "Bias statistic", Guide: 1.0);
        Deciles = new XyChart(report.SpecificDeciles
            .GroupBy(d => d.Grouping)
            .SelectMany(g => new[]
            {
                new XySeries($"{Fmt.Factor(g.Key)}: full stack", g.Select(d => d.Decile).ToList(), g.Select(d => d.FullStack).ToList(),
                    g.Key.StartsWith("size", StringComparison.Ordinal) ? Tone.Accent : Tone.Warn),
                new XySeries($"{Fmt.Factor(g.Key)}: time series only", g.Select(d => d.Decile).ToList(), g.Select(d => d.TimeSeriesOnly).ToList(),
                    Tone.Muted),
            })
            .ToList(), "Decile", "Specific-risk bias", Guide: 1.0);
    }

    protected override void OnBusyChanged() => RunCommand.NotifyCanExecuteChanged();

    [RelayCommand(CanExecute = nameof(CanRun))]
    private Task RunAsync() =>
        Start is { } a && End is { } b
            ? launcher.RunAsync(EngineJobs.Validate(DateOnly.FromDateTime(a), DateOnly.FromDateTime(b)))
            : Task.CompletedTask;

    private bool CanRun() => Start is { } a && End is { } b && a < b && !launcher.IsBusy;

    [RelayCommand]
    private void OpenReport()
    {
        if (ReportFolder is { } dir)
        {
            shell.OpenFolder(dir);
        }
    }

    private static ScoreLine Line(ScoreRow r) => new(r.Area, r.Criterion, r.Value, r.Status, Tones.Status(r.Status));

    private static BiasChart? Bias(ValidationReport report)
    {
        if (report.Factor.Count == 0)
        {
            return null;
        }

        var band = report.Factor.Select(f => f.Band).FirstOrDefault(b => b is not null) ?? 0;
        return new BiasChart(
            report.Factor.Where(f => f.Bias is not null)
                .Select(f => new BiasPoint(Fmt.Factor(f.Portfolio), f.Bias!.Value, f.Inside ?? false))
                .ToList(),
            1 - band, 1 + band);
    }
}
