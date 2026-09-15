using System.Collections.ObjectModel;
using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Pages;

/// <summary>An editable holding on the Portfolio page.</summary>
public sealed partial class HoldingRow : ObservableObject
{
    [ObservableProperty]
    public partial string Ticker { get; set; } = "";

    [ObservableProperty]
    public partial double Weight { get; set; }

    [ObservableProperty]
    public partial double? BenchWeight { get; set; }
}

public sealed record GroupRow(string Group, string Share);

public sealed record ContributionRow(
    string Factor, string Group, string Exposure, string Volatility, string Correlation, string Xsr, string Share);

public sealed record AssetRow(string Ticker, string Weight, string Mctr, string Share, string Beta);

public sealed record OptimizedRow(string Ticker, string Weight, string Benchmark, string Active);

/// <summary>Holdings files: a header with ticker and weight, and optionally bench_weight.</summary>
public static class HoldingsCsv
{
    public static (IReadOnlyList<Holding> Holdings, IReadOnlyList<string> Problems) Parse(string text)
    {
        ArgumentNullException.ThrowIfNull(text);
        var problems = new List<string>();
        var holdings = new List<Holding>();
        var lines = text.Replace("\r", "", StringComparison.Ordinal).Split('\n')
            .Select(l => l.Trim()).Where(l => l.Length > 0).ToList();
        if (lines.Count == 0)
        {
            return (holdings, ["The file is empty."]);
        }

        var header = lines[0].Split(',').Select(h => h.Trim().Trim('"').ToLowerInvariant()).ToList();
        int ticker = header.IndexOf("ticker"), weight = header.IndexOf("weight"), bench = header.IndexOf("bench_weight");
        if (ticker < 0 || weight < 0)
        {
            return (holdings, ["The first line must name the columns, including ticker and weight."]);
        }

        for (var n = 1; n < lines.Count; n++)
        {
            var cells = lines[n].Split(',').Select(c => c.Trim().Trim('"')).ToArray();
            if (cells.Length <= Math.Max(ticker, weight) || cells[ticker].Length == 0 ||
                !double.TryParse(cells[weight], NumberStyles.Float, CultureInfo.InvariantCulture, out var w))
            {
                problems.Add($"Line {n + 1} was skipped: it needs a ticker and a numeric weight.");
                continue;
            }

            double? b = bench >= 0 && bench < cells.Length &&
                        double.TryParse(cells[bench], NumberStyles.Float, CultureInfo.InvariantCulture, out var bw) ? bw : null;
            holdings.Add(new Holding(cells[ticker].ToUpperInvariant(), w, b));
        }

        return (holdings, problems);
    }
}

/// <summary>
/// Portfolio analyzer: total risk of a holdings list, or active risk when benchmark weights are
/// given, decomposed into factor groups, factors and names. The risk model runs in the engine.
/// </summary>
public sealed partial class PortfolioViewModel : PageViewModel
{
    private const int NamesShown = 20;

    private readonly IEngineFeed feed;
    private readonly IDialogs dialogs;
    private readonly IAppInfo app;

    public PortfolioViewModel(IEngineFeed feed, IDialogs dialogs, IAppInfo app, IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.dialogs = dialogs;
        this.app = app;
        Stats = [];
        Groups = [];
        Factors = [];
        Assets = [];
        Note = "";
    }

    public override string Key => "portfolio";

    public override string Title => "Portfolio analyzer";

    public override string Eyebrow => "Portfolio — Analyzer";

    public override string Heading => "Portfolio";

    public override string HeadingAccent => "risk";

    public override string? Lede =>
        "Load holdings as a CSV with ticker and weight (add bench_weight for active risk), or edit them below. Risk is " +
        "decomposed with the model of the latest published session.";

    public ObservableCollection<HoldingRow> Holdings { get; } = [];

    [ObservableProperty]
    public partial IReadOnlyList<Stat> Stats { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<GroupRow> Groups { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<ContributionRow> Factors { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<AssetRow> Assets { get; set; }

    [ObservableProperty]
    public partial BarChart? GroupChart { get; set; }

    [ObservableProperty]
    public partial string Note { get; set; }

    [ObservableProperty]
    public partial bool HasResult { get; set; }

    protected override Task LoadAsync(CancellationToken ct)
    {
        if (Holdings.Count == 0)
        {
            LoadFile(SamplePath);
        }

        return Task.CompletedTask;
    }

    private string SamplePath => Path.Combine(app.AppDirectory, "Assets", "sample_holdings.csv");

    [RelayCommand]
    private void OpenFile()
    {
        if (dialogs.PickFile("Open holdings", "CSV files (*.csv)|*.csv|All files (*.*)|*.*", null) is { } path)
        {
            LoadFile(path);
        }
    }

    [RelayCommand]
    private void UseSample() => LoadFile(SamplePath);

    [RelayCommand]
    private void AddRow() => Holdings.Add(new HoldingRow());

    [RelayCommand]
    private void Clear()
    {
        Holdings.Clear();
        HasResult = false;
    }

    [RelayCommand]
    private async Task AnalyzeAsync()
    {
        var holdings = Holdings.Where(h => !string.IsNullOrWhiteSpace(h.Ticker) && h.Weight != 0)
            .Select(h => new Holding(h.Ticker.Trim().ToUpperInvariant(), h.Weight, h.BenchWeight)).ToList();
        if (holdings.Count == 0)
        {
            await dialogs.ShowErrorAsync("No holdings", "Add at least one ticker with a weight.");
            return;
        }

        IsLoading = true;
        Problem = null;
        try
        {
            var r = await feed.PortfolioAsync(holdings);
            Show(r);
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            Problem = Problems.Describe(ex);
        }
        finally
        {
            IsLoading = false;
        }
    }

    private void LoadFile(string path)
    {
        if (!File.Exists(path))
        {
            Note = $"{path} was not found.";
            return;
        }

        var (holdings, problems) = HoldingsCsv.Parse(File.ReadAllText(path));
        Holdings.Clear();
        foreach (var h in holdings)
        {
            Holdings.Add(new HoldingRow { Ticker = h.Ticker, Weight = h.Weight, BenchWeight = h.BenchWeight });
        }

        HasResult = false;
        Note = $"{Path.GetFileName(path)}: {Fmt.Plural(holdings.Count, "holding", "holdings")}, total weight " +
               $"{Fmt.Pct(holdings.Sum(h => h.Weight))}." + (problems.Count > 0 ? " " + string.Join(" ", problems) : "");
    }

    private void Show(PortfolioReport r)
    {
        Stats =
        [
            new(r.Active ? "Active risk (annualized)" : "Total risk (annualized)", Fmt.Pct(r.SigmaAnn, 2)),
            new("Factor share of variance", Fmt.Pct(r.FactorShare)),
            new("Specific share of variance", Fmt.Pct(r.SpecificShare)),
            new(r.Active ? "Beta to the benchmark" : "Beta to the estimation universe", Fmt.Num(r.Beta, "0.000")),
            new("Model date", Fmt.Date(r.AsOf)),
        ];
        Groups = r.Groups.Select(g => new GroupRow(Fmt.Factor(g.Group), Fmt.Pct(g.PctVar))).ToList();
        GroupChart = new BarChart(r.Groups.Where(g => g.PctVar is not null)
            .Select(g => new Bar(Fmt.Factor(g.Group), g.PctVar!.Value, g.Group == "specific" ? Tone.Warn : Tone.Accent))
            .ToList(), "0%");
        Factors = r.Factors.Select(f => new ContributionRow(Fmt.Factor(f.Factor), f.Group, Fmt.WithSign(f.Exposure, "0.000"),
            Fmt.Pct(f.VolAnn), Fmt.Num(f.Corr), Fmt.Pct(f.XsrAnn, 2), Fmt.Pct(f.PctVar))).ToList();
        Assets = r.Assets.Take(NamesShown).Select(a => new AssetRow(a.Ticker ?? "", Fmt.Pct(a.Weight, 2), Fmt.Pct(a.MctrAnn, 2),
            Fmt.Pct(a.PctRisk), Fmt.Num(a.Beta))).ToList();
        Note = r.Unmatched.Count > 0
            ? $"Not in the model on {Fmt.Date(r.AsOf)}: {string.Join(", ", r.Unmatched)}."
            : $"All {Fmt.Plural(r.Matched, "holding", "holdings")} matched.";
        HasResult = true;
    }
}

/// <summary>
/// Optimizer: against the cap-weighted estimation universe, minimize active risk (or tilt toward a
/// style) under exposure bands, a weight cap, a tracking-error cap and a turnover limit.
/// </summary>
public sealed partial class OptimizerViewModel : PageViewModel
{
    public const string NoTilt = "None: minimum active risk";

    private readonly IEngineFeed feed;

    public OptimizerViewModel(IEngineFeed feed, IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        Methods = ["Factor form (cvxpy)", "Riskfolio-Lib"];
        Method = Methods[0];
        Tilts = [NoTilt];
        Tilt = NoTilt;
        AlphaPercent = 1.0;
        TrackingErrorPercent = 3.0;
        StyleBand = 0.10;
        IndustryBand = 0.02;
        MaxWeightPercent = 5.0;
        Turnover = 1.0;
        Stats = [];
        Exposures = [];
        Result = [];
        Status = "";
    }

    public override string Key => "optimizer";

    public override string Title => "Optimizer";

    public override string Eyebrow => "Portfolio — Optimizer";

    public override string Heading => "Portfolio";

    public override string HeadingAccent => "construction";

    public override string? Lede =>
        "Benchmark: the estimation universe, cap-weighted. Riskfolio minimizes total risk under the bands; its tracking " +
        "error is historical, so the tracking-error cap and turnover limit apply to the factor form only (§12.3).";

    public IReadOnlyList<string> Methods { get; }

    [ObservableProperty]
    public partial string Method { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<string> Tilts { get; set; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasTilt))]
    public partial string Tilt { get; set; }

    public bool HasTilt => Tilt != NoTilt;

    [ObservableProperty]
    public partial double AlphaPercent { get; set; }

    [ObservableProperty]
    public partial double TrackingErrorPercent { get; set; }

    [ObservableProperty]
    public partial double StyleBand { get; set; }

    [ObservableProperty]
    public partial double IndustryBand { get; set; }

    [ObservableProperty]
    public partial double MaxWeightPercent { get; set; }

    [ObservableProperty]
    public partial double Turnover { get; set; }

    [ObservableProperty]
    public partial bool IsSolving { get; set; }

    [ObservableProperty]
    public partial bool HasResult { get; set; }

    [ObservableProperty]
    public partial string Status { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<Stat> Stats { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<ContributionRow> Exposures { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<OptimizedRow> Result { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var day = await feed.DayAsync(ct: ct);
        Tilts = [NoTilt, .. day.Moves.Where(m => m.Group == "style").Select(m => m.Factor).Order(StringComparer.Ordinal)];
    }

    [RelayCommand]
    private async Task OptimizeAsync()
    {
        IsSolving = true;
        Problem = null;
        Status = "Solving in the engine…";
        try
        {
            var request = new OptimizeRequest(
                Method.StartsWith("Factor", StringComparison.Ordinal) ? "factor" : "riskfolio",
                TrackingErrorPercent / 100, StyleBand, IndustryBand, MaxWeightPercent / 100, Turnover,
                HasTilt ? Tilt : null, HasTilt ? AlphaPercent / 100 : 0.0);
            Show(await feed.OptimizeAsync(request));
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            Problem = Problems.Describe(ex);
            Status = "";
        }
        finally
        {
            IsSolving = false;
        }
    }

    private void Show(OptimizeOutcome o)
    {
        HasResult = o.Ok;
        Status = o.Ok ? $"Solved: {o.Status}." : $"No solution: {o.Status}. Loosen a band or a cap and try again.";
        if (!o.Ok)
        {
            return;
        }

        Stats =
        [
            new("Active risk (annualized)", Fmt.Pct(o.SigmaAnn, 2)),
            new("Names held", Fmt.Count(o.NamesHeld)),
            new("Turnover against the benchmark", Fmt.Num(o.Turnover)),
            new("Beta to the benchmark", Fmt.Num(o.Beta, "0.000")),
            new("Model date", Fmt.Date(o.AsOf)),
        ];
        Exposures = (o.Factors ?? []).Select(f => new ContributionRow(Fmt.Factor(f.Factor), f.Group, Fmt.WithSign(f.Exposure, "0.000"),
            Fmt.Pct(f.VolAnn), Fmt.Num(f.Corr), Fmt.Pct(f.XsrAnn, 2), Fmt.Pct(f.PctVar))).ToList();
        Result = (o.Holdings ?? []).Select(h => new OptimizedRow(h.Ticker ?? "", Fmt.Pct(h.Weight, 2), Fmt.Pct(h.Benchmark, 2),
            Fmt.SignedPct(h.Weight - h.Benchmark))).ToList();
    }
}
