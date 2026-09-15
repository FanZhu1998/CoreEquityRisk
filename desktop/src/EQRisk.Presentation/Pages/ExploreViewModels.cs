using System.Data;
using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Presentation.Charts;

namespace EQRisk.Presentation.Pages;

/// <summary>A factor that can be shown or hidden on a chart.</summary>
public sealed partial class FactorToggle : ObservableObject
{
    public FactorToggle(string code, string group, bool selected)
    {
        Code = code;
        Group = group;
        Label = Fmt.Factor(code);
        IsSelected = selected;
    }

    public string Code { get; }

    public string Group { get; }

    public string Label { get; }

    [ObservableProperty]
    public partial bool IsSelected { get; set; }
}

public sealed record FactorVolRow(string Factor, string Group, double? VolAnn, double? PreEigenAnn, double? Change);

public sealed record SpecificLine(
    string Ticker, string Industry, bool InEstu, double? TimeSeries, double? Structural, double? Blend, double? Final,
    double? Gamma);

/// <summary>
/// The Explore pages all look at one as-of date. The date snaps to the nearest model date on or
/// before the one picked, and defaults to the latest published session.
/// </summary>
public abstract partial class DatedPageViewModel : PageViewModel
{
    private IReadOnlyList<DateOnly> modelDates = [];
    private bool settingDate;

    protected DatedPageViewModel(IEngineFeed feed, IMessenger messenger)
        : base(messenger) => Feed = feed;

    [ObservableProperty]
    public partial DateTime? AsOf { get; set; }

    [ObservableProperty]
    public partial DateTime? FirstDate { get; set; }

    [ObservableProperty]
    public partial DateTime? LastDate { get; set; }

    protected IEngineFeed Feed { get; }

    /// <summary>The model date to show: the picked date snapped back to a date the model has.</summary>
    protected async Task<DateOnly> ResolveDateAsync(CancellationToken ct)
    {
        if (modelDates.Count == 0)
        {
            var dates = await Feed.DatesAsync(ct);
            modelDates = dates.Dates;
            if (modelDates.Count == 0)
            {
                throw new InvalidOperationException("The model has no dates yet.");
            }

            FirstDate = modelDates[0].ToDateTime(TimeOnly.MinValue);
            LastDate = modelDates[^1].ToDateTime(TimeOnly.MinValue);
            SetDate(dates.Latest ?? modelDates[^1]);
        }

        var wanted = AsOf is { } a ? DateOnly.FromDateTime(a) : modelDates[^1];
        var snapped = Snap(modelDates, wanted);
        SetDate(snapped);
        return snapped;
    }

    public static DateOnly Snap(IReadOnlyList<DateOnly> dates, DateOnly wanted)
    {
        ArgumentNullException.ThrowIfNull(dates);
        var i = BinarySearch(dates, wanted);
        return i >= 0 ? dates[i] : dates[Math.Max(0, ~i - 1)];
    }

    partial void OnAsOfChanged(DateTime? value)
    {
        if (!settingDate && value is not null)
        {
            _ = ReloadAsync();
        }
    }

    private void SetDate(DateOnly d)
    {
        settingDate = true;
        AsOf = d.ToDateTime(TimeOnly.MinValue);
        settingDate = false;
    }

    private static int BinarySearch(IReadOnlyList<DateOnly> dates, DateOnly wanted)
    {
        int lo = 0, hi = dates.Count - 1;
        while (lo <= hi)
        {
            var mid = (lo + hi) >>> 1;
            var c = dates[mid].CompareTo(wanted);
            if (c == 0)
            {
                return mid;
            }

            if (c < 0)
            {
                lo = mid + 1;
            }
            else
            {
                hi = mid - 1;
            }
        }

        return ~lo;
    }
}

/// <summary>Cumulative factor returns over time and the factor returns of the as-of session.</summary>
public sealed partial class FactorReturnsViewModel : DatedPageViewModel
{
    private HistoryInfo? history;

    public FactorReturnsViewModel(IEngineFeed feed, IMessenger messenger)
        : base(feed, messenger)
    {
        Factors = [];
        Returns = [];
        Years = 1;
    }

    public override string Key => "factor-returns";

    public override string Title => "Factor returns";

    public override string Eyebrow => "Explore — Factor returns";

    public override string Heading => "Factor";

    public override string HeadingAccent => "returns";

    public override string? Lede =>
        "Daily returns of the pure factor portfolios from the cross-sectional regression, compounded over the window.";

    public IReadOnlyList<int> YearChoices { get; } = [1, 3, 5];

    [ObservableProperty]
    public partial int Years { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<FactorToggle> Factors { get; set; }

    [ObservableProperty]
    public partial TimeChart? Cumulative { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<FactorReturnRow> Returns { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var d = await ResolveDateAsync(ct);
        history = await Feed.HistoryAsync(Years, d, ct);
        var day = await Feed.DayAsync(d, ct);
        if (Factors.Count == 0)
        {
            Factors = day.Moves
                .OrderBy(m => m.Group == "style" ? 0 : m.Group == "country" ? 1 : 2).ThenBy(m => m.Factor, StringComparer.Ordinal)
                .Select(m => Watch(new FactorToggle(m.Factor, m.Group, m.Group == "style")))
                .ToList();
        }

        Returns = day.Moves.OrderByDescending(m => m.F ?? double.NegativeInfinity)
            .Select(m => new FactorReturnRow(Fmt.Factor(m.Factor), m.Group, Fmt.SignedPct(m.F), Fmt.WithSign(m.TStat),
                Fmt.WithSign(m.FOverSigma), Tones.Sign(m.F)))
            .ToList();
        Redraw();
    }

    partial void OnYearsChanged(int value) => _ = ReloadAsync();

    [RelayCommand]
    private void Show(string group)
    {
        foreach (var f in Factors)
        {
            f.IsSelected = group switch
            {
                "none" => false,
                "all" => true,
                _ => f.Group == group,
            };
        }
    }

    private FactorToggle Watch(FactorToggle toggle)
    {
        toggle.PropertyChanged += (_, _) => Redraw();
        return toggle;
    }

    private void Redraw()
    {
        if (history is null)
        {
            return;
        }

        Cumulative = new TimeChart(
            Factors.Where(f => f.IsSelected && history.Cum.ContainsKey(f.Code))
                .Select(f => new DatedSeries(f.Label, history.Dates, history.Cum[f.Code], Tone.Categorical))
                .ToList(),
            Guide: 0, ValueFormat: "0%");
    }
}

/// <summary>Annualized factor volatilities, before and after the eigen adjustment, and correlations.</summary>
public sealed partial class FactorRiskViewModel : DatedPageViewModel
{
    public FactorRiskViewModel(IEngineFeed feed, IMessenger messenger)
        : base(feed, messenger) => Rows = [];

    public override string Key => "factor-risk";

    public override string Title => "Factor risk";

    public override string Eyebrow => "Explore — Factor risk";

    public override string Heading => "Factor";

    public override string HeadingAccent => "covariance";

    public override string? Lede =>
        "The four-layer factor covariance: exponentially weighted with Newey-West, the eigenfactor adjustment, and the " +
        "volatility regime adjustment. Volatilities are annualized.";

    [ObservableProperty]
    public partial IReadOnlyList<FactorVolRow> Rows { get; set; }

    [ObservableProperty]
    public partial Heatmap? Correlations { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var d = await ResolveDateAsync(ct);
        var risk = await Feed.FactorRiskAsync(d, ct);
        Rows = risk.Factors
            .Select(f => new FactorVolRow(Fmt.Factor(f.Factor), f.Group, f.VolAnn, f.VolPreEigenAnn,
                f.VolAnn is { } a && f.VolPreEigenAnn is { } b && b > 0 ? a / b - 1 : null))
            .ToList();
        Correlations = new Heatmap(risk.Factors.Select(f => Fmt.Factor(f.Factor)).ToList(), risk.Corr);
    }
}

/// <summary>Every name's style exposures on the date, searchable and sortable.</summary>
public sealed partial class ExposuresViewModel : DatedPageViewModel
{
    private IReadOnlyList<string> styles = [];

    public ExposuresViewModel(IEngineFeed feed, IMessenger messenger)
        : base(feed, messenger)
    {
        Search = "";
        Summary = "";
    }

    public override string Key => "exposures";

    public override string Title => "Exposures";

    public override string Eyebrow => "Explore — Exposures";

    public override string Heading => "Style";

    public override string HeadingAccent => "exposures";

    public override string? Lede =>
        "Standardized style exposures of every name in coverage: cap-weighted mean zero and equal-weighted unit " +
        "variance over the estimation universe.";

    [ObservableProperty]
    public partial DataView? Table { get; set; }

    [ObservableProperty]
    public partial string Search { get; set; }

    [ObservableProperty]
    public partial DataRowView? Selected { get; set; }

    [ObservableProperty]
    public partial BarChart? SelectedExposures { get; set; }

    [ObservableProperty]
    public partial string Summary { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var d = await ResolveDateAsync(ct);
        var info = await Feed.ExposuresAsync(d, ct);
        styles = info.Styles;
        using var table = new DataTable("exposures") { Locale = CultureInfo.InvariantCulture };
        table.Columns.Add("Ticker", typeof(string));
        table.Columns.Add("Industry", typeof(string));
        table.Columns.Add("ESTU", typeof(bool));
        foreach (var s in styles)
        {
            table.Columns.Add(Fmt.Factor(s), typeof(double));
        }

        foreach (var row in info.Rows)
        {
            var values = new object?[3 + styles.Count];
            values[0] = row.Ticker ?? "";
            values[1] = row.Industry is { } ind ? Fmt.Factor(ind) : "";
            values[2] = row.InEstu ?? false;
            for (var i = 0; i < styles.Count; i++)
            {
                values[3 + i] = row.Style(styles[i]) is { } v ? v : DBNull.Value;
            }

            table.Rows.Add(values);
        }

        Table = table.DefaultView;
        ApplySearch();
        Summary = $"{Fmt.Plural(info.Rows.Count, "name", "names")}, {Fmt.Count(info.Rows.Count(r => r.InEstu == true))} in the estimation universe";
    }

    partial void OnSearchChanged(string value) => ApplySearch();

    partial void OnSelectedChanged(DataRowView? value)
    {
        if (value is null)
        {
            SelectedExposures = null;
            return;
        }

        SelectedExposures = new BarChart(styles
            .Select(s => (Name: Fmt.Factor(s), Value: value[Fmt.Factor(s)]))
            .Where(p => p.Value is double)
            .Select(p => new Bar(p.Name, (double)p.Value, Tones.Sign((double)p.Value)))
            .Reverse()
            .ToList(), "+0.00;-0.00", Guide: 0);
    }

    private void ApplySearch()
    {
        if (Table is null)
        {
            return;
        }

        var text = Search.Trim().Replace("'", "''", StringComparison.Ordinal)
            .Replace("[", "[[]", StringComparison.Ordinal).Replace("%", "[%]", StringComparison.Ordinal)
            .Replace("*", "[*]", StringComparison.Ordinal);
        Table.RowFilter = text.Length == 0 ? "" : $"Ticker LIKE '{text}%' OR Industry LIKE '%{text}%'";
    }
}

/// <summary>Every specific-risk layer on the date, and how final specific volatility is spread.</summary>
public sealed partial class SpecificRiskViewModel : DatedPageViewModel
{
    private static readonly double[] Edges = [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50];
    private IReadOnlyList<SpecificLine> all = [];

    public SpecificRiskViewModel(IEngineFeed feed, IMessenger messenger)
        : base(feed, messenger)
    {
        Rows = [];
        Search = "";
        Summary = "";
    }

    public override string Key => "specific-risk";

    public override string Title => "Specific risk";

    public override string Eyebrow => "Explore — Specific risk";

    public override string Heading => "Specific";

    public override string HeadingAccent => "risk";

    public override string? Lede =>
        "Five layers: the time-series forecast, the structural model for thin histories, their blend, Bayesian " +
        "shrinkage toward size-decile means, and the volatility regime adjustment. Annualized.";

    [ObservableProperty]
    public partial IReadOnlyList<SpecificLine> Rows { get; set; }

    [ObservableProperty]
    public partial string Search { get; set; }

    [ObservableProperty]
    public partial BarChart? Distribution { get; set; }

    [ObservableProperty]
    public partial string Summary { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var d = await ResolveDateAsync(ct);
        var info = await Feed.SpecificAsync(d, ct);
        all = info.Rows.Select(r => new SpecificLine(r.Ticker ?? "", r.Industry is { } i ? Fmt.Factor(i) : "",
            r.InEstu ?? false, r.SigmaTs, r.SigmaStr, r.SigmaBlend, r.SigmaFinal, r.Gamma)).ToList();
        ApplySearch();
        var finals = all.Where(r => r.InEstu && r.Final is not null).Select(r => r.Final!.Value).Order().ToList();
        Summary = finals.Count == 0 ? ""
            : $"Estimation universe median {Fmt.Pct(finals[finals.Count / 2])}, " +
              $"interquartile {Fmt.Pct(finals[finals.Count / 4])} to {Fmt.Pct(finals[finals.Count * 3 / 4])}";
        Distribution = Histogram(all.Where(r => r.Final is not null).Select(r => r.Final!.Value).ToList());
    }

    partial void OnSearchChanged(string value) => ApplySearch();

    private void ApplySearch()
    {
        var text = Search.Trim();
        Rows = text.Length == 0 ? all : all.Where(r =>
            r.Ticker.StartsWith(text, StringComparison.OrdinalIgnoreCase) ||
            r.Industry.Contains(text, StringComparison.OrdinalIgnoreCase)).ToList();
    }

    private static BarChart Histogram(IReadOnlyList<double> values)
    {
        var bars = new List<Bar>();
        var lower = 0.0;
        foreach (var edge in Edges.Append(double.PositiveInfinity))
        {
            var count = values.Count(v => v >= lower && v < edge);
            var label = double.IsPositiveInfinity(edge) ? $"{Fmt.Pct(lower, 0)} and above" : $"{Fmt.Pct(lower, 0)} to {Fmt.Pct(edge, 0)}";
            bars.Add(new Bar(label, count, Tone.Accent));
            lower = edge;
        }

        bars.Reverse();
        return new BarChart(bars, "0");
    }
}
