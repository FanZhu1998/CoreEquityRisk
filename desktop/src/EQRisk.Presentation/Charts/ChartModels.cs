namespace EQRisk.Presentation.Charts;

// What a chart shows, with no drawing library in sight. The Desktop project renders these with
// ScottPlot in the app's palette; view models and their tests only ever see these records.

/// <summary>A colour role from the palette, not a colour: the view decides what each looks like.</summary>
public enum Tone
{
    Accent,
    AccentSoft,
    Bull,
    Bear,
    Warn,
    Muted,
    Ink,

    /// <summary>One of many series: the view gives each its own colour from the categorical palette.</summary>
    Categorical,
}

/// <summary>A dated series. Missing values (null) leave a gap instead of a false zero.</summary>
public sealed record DatedSeries(string Name, IReadOnlyList<DateOnly> Dates, IReadOnlyList<double?> Values, Tone Tone);

/// <summary>Lines over dates, with an optional horizontal guide (1.0 for a regime multiplier).</summary>
public sealed record TimeChart(IReadOnlyList<DatedSeries> Series, double? Guide = null, string ValueFormat = "0.00");

public sealed record Bar(string Label, double Value, Tone Tone);

/// <summary>Bars, horizontal by default so long factor names stay readable.</summary>
public sealed record BarChart(IReadOnlyList<Bar> Bars, string ValueFormat = "0.00", double? Guide = null);

/// <summary>A line over numbered categories (eigenfactor rank, decile).</summary>
public sealed record XySeries(string Name, IReadOnlyList<double> X, IReadOnlyList<double?> Y, Tone Tone);

/// <summary>Lines over numbered categories, with an optional guide line and shaded band around it.</summary>
public sealed record XyChart(
    IReadOnlyList<XySeries> Series, string XLabel, string YLabel, double? Guide = null,
    double? BandLow = null, double? BandHigh = null);

/// <summary>Bias statistics as points with a ±band around 1: the factor-portfolio battery.</summary>
public sealed record BiasPoint(string Label, double Bias, bool Inside);

public sealed record BiasChart(IReadOnlyList<BiasPoint> Points, double BandLow, double BandHigh);

/// <summary>A square matrix of values in [-1, 1] with row and column labels (factor correlations).</summary>
public sealed record Heatmap(IReadOnlyList<string> Labels, IReadOnlyList<IReadOnlyList<double?>> Values);
