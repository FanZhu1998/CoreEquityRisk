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
/// Estimate: catch up, re-estimate one session from the data source of your choice, rebuild the
/// whole model history after a parameter change, and the model's health over time.
/// </summary>
public sealed partial class EstimateViewModel : PageViewModel
{
    private const int HealthYears = 3;

    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly IDialogs dialogs;

    public EstimateViewModel(IEngineFeed feed, JobLauncher launcher, IDialogs dialogs, IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.launcher = launcher;
        this.dialogs = dialogs;
        PendingText = "";
        Returns = [];
        Sources = [DataSource.StoredData, DataSource.FreshDownload, DataSource.StoredStaging];
        Source = DataSource.StoredData;
    }

    public override string Key => "estimate";

    public override string Title => "Estimate";

    public override string Eyebrow => "03 — Estimate";

    public override string Heading => "Factor";

    public override string HeadingAccent => "estimation";

    public override string? Lede =>
        "Country, twenty industries and twelve styles by cap-weighted constrained regression, with a four-layer factor " +
        "covariance and five-layer specific risk at a one-month horizon.";

    public IReadOnlyList<string> Pipeline { get; } =
        ["Descriptors & exposures", "Factor returns (WLS)", "Factor covariance", "Specific risk", "Quality gates"];

    public IReadOnlyList<DataSource> Sources { get; }

    [ObservableProperty]
    public partial string PendingText { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(CatchUpCommand))]
    public partial bool HasPending { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(ReestimateCommand))]
    public partial DateTime? Session { get; set; }

    [ObservableProperty]
    public partial DateTime? LatestSession { get; set; }

    [ObservableProperty]
    public partial DataSource Source { get; set; }

    [ObservableProperty]
    public partial DateOnly? RebuildThrough { get; set; }

    [ObservableProperty]
    public partial TimeChart? Fit { get; set; }

    [ObservableProperty]
    public partial TimeChart? Regime { get; set; }

    [ObservableProperty]
    public partial string ReturnsTitle { get; set; } = "";

    [ObservableProperty]
    public partial IReadOnlyList<FactorReturnRow> Returns { get; set; }

    public static string Describe(DataSource source) => source switch
    {
        DataSource.FreshDownload => "Fresh download",
        DataSource.StoredData => "Stored data",
        _ => "Stored staging (fastest)",
    };

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var status = await feed.StatusAsync(ct);
        var inventory = await feed.InventoryAsync(ct);
        HasPending = !status.UpToDate;
        PendingText = status.UpToDate
            ? $"Nothing pending: the model is estimated through {Fmt.Date(status.Last)}."
            : $"Pending: {string.Join(", ", status.Pending.Select(d => Fmt.Date(d)))}. The model is estimated through {Fmt.Date(status.Last)}.";
        if (status.Last is { } last)
        {
            LatestSession = last.ToDateTime(TimeOnly.MinValue);
            Session ??= LatestSession;
        }

        RebuildThrough = inventory.EodLast ?? status.Last;
        if (status.Last is null)
        {
            return;
        }

        var history = await feed.HistoryAsync(HealthYears, ct: ct);
        var day = await feed.DayAsync(status.Last, ct);
        Fit = new TimeChart([new DatedSeries("Weighted R²", history.R2Dates, history.R2, Tone.Accent)], ValueFormat: "0%");
        Regime = new TimeChart(
        [
            new DatedSeries("λF factor", history.VraDates, history.LambdaF, Tone.Accent),
            new DatedSeries("λS specific", history.VraDates, history.LambdaS, Tone.Warn),
        ], Guide: 1.0);
        ReturnsTitle = $"Factor returns on {Fmt.Date(day.Date)}";
        Returns = day.Moves
            .OrderBy(m => m.Group == "country" ? 0 : m.Group == "style" ? 1 : 2).ThenBy(m => m.Factor, StringComparer.Ordinal)
            .Select(m => new FactorReturnRow(Fmt.Factor(m.Factor), m.Group, Fmt.SignedPct(m.F), Fmt.WithSign(m.TStat),
                Fmt.WithSign(m.FOverSigma), Tones.Sign(m.F)))
            .ToList();
    }

    protected override void OnBusyChanged()
    {
        CatchUpCommand.NotifyCanExecuteChanged();
        ReestimateCommand.NotifyCanExecuteChanged();
        RebuildModelCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand(CanExecute = nameof(CanCatchUp))]
    private async Task CatchUpAsync() => await launcher.RunAsync(EngineJobs.DailyUpdate());

    private bool CanCatchUp() => HasPending && !launcher.IsBusy;

    [RelayCommand(CanExecute = nameof(CanReestimate))]
    private Task ReestimateAsync() =>
        Session is { } d ? launcher.RunAsync(EngineJobs.Reestimate(DateOnly.FromDateTime(d), Source)) : Task.CompletedTask;

    private bool CanReestimate() => Session is not null && !launcher.IsBusy;

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task RebuildModelAsync()
    {
        if (RebuildThrough is not { } through)
        {
            return;
        }

        if (await dialogs.ConfirmAsync("Rebuild the model history?",
                $"Recomputes exposures, factor returns, the factor covariance and specific risk for every session through " +
                $"{Fmt.Date(through)} and replaces the model tables. It takes about an hour. Use it after changing " +
                "parameters in configs/model_us_lc.yaml.", "Rebuild", ConfirmTone.Destructive))
        {
            await launcher.RunAsync(EngineJobs.RebuildModel(through));
        }
    }

    private bool Idle() => !launcher.IsBusy;
}
