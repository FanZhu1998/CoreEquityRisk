using System.Globalization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using EQRisk.Presentation.Charts;
using ScottPlot.TickGenerators;
using ScottPlot.WPF;
using ChartHeatmap = EQRisk.Presentation.Charts.Heatmap;

namespace EQRisk.Desktop.Views.Controls;

/// <summary>
/// Draws any chart model the view models produce, with ScottPlot, in the app's palette and font.
/// Charts do not zoom or pan, so the page scrolls straight through them; the correlation heatmap
/// shows a cell's value on hover.
/// </summary>
public sealed class ChartView : UserControl
{
    public static readonly DependencyProperty ChartProperty = DependencyProperty.Register(nameof(Chart), typeof(object),
        typeof(ChartView), new PropertyMetadata(null, (d, _) => ((ChartView)d).Render()));

    // The site's categorical chart colours (.streamlit/config.toml, chartCategoricalColors).
    private static readonly string[] Categorical =
        ["#7DABDD", "#46BF94", "#E5776C", "#A7C8EE", "#E2B86B", "#628AB8", "#B0D2F0", "#C39BD3", "#7F92A9", "#4D8BC4"];

    private static readonly (string Name, string File)[] FontFiles =
        [("Inter", "Inter-Regular.ttf"), ("Libre Franklin", "LibreFranklin-SemiBold.ttf")];

    private static bool fontsLoaded;
    private readonly WpfPlot view = new();
    private IReadOnlyList<string> heatLabels = [];
    private IReadOnlyList<IReadOnlyList<double?>> heatValues = [];

    public ChartView()
    {
        LoadFonts();
        Content = view;
        view.UserInputProcessor.Disable();
        view.MouseMove += OnMouseMove;
        view.MouseLeave += (_, _) => ToolTip = null;
        Render();
    }

    public object? Chart
    {
        get => GetValue(ChartProperty);
        set => SetValue(ChartProperty, value);
    }

    private static ScottPlot.Color Palette(string key)
    {
        var c = PaletteColors.Get(key);
        return new ScottPlot.Color(c.R, c.G, c.B, c.A);
    }

    private static ScottPlot.Color ColorOf(Tone tone, int index) => tone switch
    {
        Tone.Categorical => ScottPlot.Color.FromHex(Categorical[index % Categorical.Length]),
        Tone.Bull => Palette("BullColor"),
        Tone.Bear => Palette("BearColor"),
        Tone.Warn => Palette("WarnColor"),
        Tone.Muted => Palette("MutedColor"),
        Tone.Ink => Palette("InkColor"),
        Tone.AccentSoft => Palette("AccentHiColor"),
        _ => Palette("AccentColor"),
    };

    private static (double[] Xs, double[] Ys) Points(IReadOnlyList<double> xs, IReadOnlyList<double?> ys)
    {
        var keep = Enumerable.Range(0, Math.Min(xs.Count, ys.Count)).Where(i => ys[i] is { } v && double.IsFinite(v)).ToArray();
        return (keep.Select(i => xs[i]).ToArray(), keep.Select(i => ys[i]!.Value).ToArray());
    }

    private static NumericAutomatic Formatted(string format) =>
        new() { LabelFormatter = v => v.ToString(format, CultureInfo.CurrentCulture) };

    private static void LoadFonts()
    {
        if (fontsLoaded)
        {
            return;
        }

        fontsLoaded = true;
        var dir = Path.Combine(AppContext.BaseDirectory, "Assets", "Fonts");
        foreach (var (name, file) in FontFiles)
        {
            var path = Path.Combine(dir, file);
            if (File.Exists(path))
            {
                ScottPlot.Fonts.AddFontFile(name, path, false, false);
            }
        }
    }

    private void Render()
    {
        // A fresh plot each time: Clear() would keep panels (the colour bar, an outside legend) and axis settings.
        view.Reset();
        view.UserInputProcessor.Disable();
        var p = view.Plot;
        heatLabels = [];
        heatValues = [];
        view.Visibility = Chart is null ? Visibility.Collapsed : Visibility.Visible;
        switch (Chart)
        {
            case TimeChart t:
                Time(p, t);
                break;
            case BarChart b:
                Bars(p, b);
                break;
            case XyChart x:
                Xy(p, x);
                break;
            case BiasChart b:
                Bias(p, b);
                break;
            case ChartHeatmap h:
                Heat(p, h);
                break;
        }

        Theme(p);
        view.Refresh();
    }

    private static void Theme(ScottPlot.Plot p)
    {
        var surface = Palette("SurfaceColor");
        p.FigureBackground.Color = surface;
        p.DataBackground.Color = surface;
        p.Font.Set("Inter");
        p.Axes.Color(Palette("MutedColor"));
        p.Axes.FrameColor(Palette("Surface3Color"));
        p.Grid.MajorLineColor = Palette("Surface3Color");
        p.Legend.BackgroundColor = Palette("Surface2Color");
        p.Legend.FontColor = Palette("Ink2Color");
        p.Legend.OutlineColor = Palette("Surface3Color");
        p.Legend.FontName = "Inter";
        p.Legend.FontSize = 11;
        foreach (ScottPlot.IAxis axis in new ScottPlot.IAxis[] { p.Axes.Bottom, p.Axes.Left })
        {
            axis.TickLabelStyle.FontSize = axis.TickLabelStyle.FontSize > 0 ? Math.Min(axis.TickLabelStyle.FontSize, 11) : 11;
            axis.Label.ForeColor = Palette("MutedColor");
            axis.Label.FontSize = 11;
        }
    }

    private static void Guide(ScottPlot.Plot p, double y) =>
        p.Add.HorizontalLine(y, 1, Palette("Line2Color"), ScottPlot.LinePattern.Dashed);

    private static void Time(ScottPlot.Plot p, TimeChart t)
    {
        var index = 0;
        foreach (var s in t.Series)
        {
            var (xs, ys) = Points(s.Dates.Select(d => d.ToDateTime(TimeOnly.MinValue).ToOADate()).ToList(), s.Values);
            if (xs.Length == 0)
            {
                continue;
            }

            var line = p.Add.ScatterLine(xs, ys, ColorOf(s.Tone, index++));
            line.LineWidth = 1.8f;
            line.LegendText = s.Name;
        }

        if (t.Guide is { } g)
        {
            Guide(p, g);
        }

        p.Axes.DateTimeTicksBottom();
        p.Axes.Left.TickGenerator = Formatted(t.ValueFormat);
        Legend(p, index, ScottPlot.Alignment.UpperLeft, ScottPlot.Edge.Right);
    }

    // One series needs no legend; two fit in a corner; more go beside or under the data, where no
    // line can hide beneath them.
    private static void Legend(ScottPlot.Plot p, int series, ScottPlot.Alignment corner, ScottPlot.Edge outside)
    {
        if (series < 2)
        {
            return;
        }

        if (series == 2)
        {
            p.ShowLegend(corner);
            return;
        }

        if (outside is ScottPlot.Edge.Bottom or ScottPlot.Edge.Top)
        {
            p.Legend.Orientation = ScottPlot.Orientation.Horizontal;   // wraps onto more lines as needed
        }

        p.ShowLegend(outside);
    }

    private static void Bars(ScottPlot.Plot p, BarChart b)
    {
        var bars = b.Bars.Select((bar, i) => new ScottPlot.Bar
        {
            Position = i,
            Value = bar.Value,
            FillColor = ColorOf(bar.Tone, i),
            LineWidth = 0,
            Size = 0.62,
        }).ToList();
        var plotted = p.Add.Bars(bars);
        plotted.Horizontal = true;
        var ticks = new NumericManual();
        for (var i = 0; i < b.Bars.Count; i++)
        {
            ticks.AddMajor(i, b.Bars[i].Label);
        }

        p.Axes.Left.TickGenerator = ticks;
        p.Axes.Bottom.TickGenerator = Formatted(b.ValueFormat);
        if (b.Guide is { } g)
        {
            p.Add.VerticalLine(g, 1, Palette("Line2Color"));
        }
    }

    private static void Xy(ScottPlot.Plot p, XyChart c)
    {
        if (c.BandLow is { } lo && c.BandHigh is { } hi)
        {
            p.Add.HorizontalLine(lo, 1, Palette("MutedColor"), ScottPlot.LinePattern.Dotted);
            p.Add.HorizontalLine(hi, 1, Palette("MutedColor"), ScottPlot.LinePattern.Dotted);
        }

        if (c.Guide is { } g)
        {
            Guide(p, g);
        }

        var index = 0;
        foreach (var s in c.Series)
        {
            var (xs, ys) = Points(s.X, s.Y);
            var line = p.Add.Scatter(xs, ys, ColorOf(s.Tone, index++));
            line.LineWidth = 1.8f;
            line.MarkerSize = 5;
            line.LegendText = s.Name;
        }

        if (c.Series.All(s => s.X.All(x => x == Math.Round(x))))
        {
            p.Axes.Bottom.TickGenerator = new NumericAutomatic { IntegerTicksOnly = true };   // ranks and deciles
        }

        p.XLabel(c.XLabel);
        p.YLabel(c.YLabel);
        Legend(p, index, ScottPlot.Alignment.UpperRight, ScottPlot.Edge.Bottom);
    }

    // Factors down the side, the bias along the bottom, the ±band where sampling error alone would put it.
    private static void Bias(ScottPlot.Plot p, BiasChart c)
    {
        var n = c.Points.Count;
        double Row(int i) => n - 1 - i;
        p.Add.VerticalLine(c.BandLow, 1, Palette("MutedColor"), ScottPlot.LinePattern.Dotted);
        p.Add.VerticalLine(c.BandHigh, 1, Palette("MutedColor"), ScottPlot.LinePattern.Dotted);
        p.Add.VerticalLine(1.0, 1.2f, Palette("Ink2Color"), ScottPlot.LinePattern.Dashed);
        foreach (var inside in new[] { true, false })
        {
            var idx = Enumerable.Range(0, n).Where(i => c.Points[i].Inside == inside).ToArray();
            if (idx.Length > 0)
            {
                p.Add.Markers(idx.Select(i => c.Points[i].Bias).ToArray(), idx.Select(Row).ToArray(),
                    ScottPlot.MarkerShape.FilledCircle, 8, inside ? Palette("BullColor") : Palette("BearColor"));
            }
        }

        var ticks = new NumericManual();
        for (var i = 0; i < n; i++)
        {
            ticks.AddMajor(Row(i), c.Points[i].Label);
        }

        p.Axes.Left.TickGenerator = ticks;
        p.Axes.Bottom.TickGenerator = Formatted("0.00");
    }

    private void Heat(ScottPlot.Plot p, ChartHeatmap h)
    {
        var n = h.Labels.Count;
        var data = new double[n, n];
        for (var r = 0; r < n; r++)
        {
            for (var c = 0; c < n; c++)
            {
                data[r, c] = h.Values[r][c] ?? double.NaN;
            }
        }

        var map = p.Add.Heatmap(data);
        map.Colormap = new DivergingColormap(Palette("BearColor"), Palette("Surface3Color"), Palette("AccentColor"));
        map.ManualRange = new ScottPlot.Range(-1, 1);
        map.Extent = new ScottPlot.CoordinateRect(-0.5, n - 0.5, -0.5, n - 0.5);
        var left = new NumericManual();
        for (var i = 0; i < n; i++)
        {
            left.AddMajor(n - 1 - i, h.Labels[i]);
        }

        // Rows are named down the side. The matrix is symmetric and a hover names both factors, so the
        // columns stay unlabelled rather than carry 34 unreadable rotated names.
        p.Axes.Left.TickGenerator = left;
        p.Axes.Bottom.TickGenerator = new NumericManual();
        p.Axes.Left.TickLabelStyle.FontSize = 9.5f;
        p.HideGrid();
        p.Axes.SetLimits(-0.5, n - 0.5, -0.5, n - 0.5);
        var bar = p.Add.ColorBar(map);
        var scale = new NumericManual();
        foreach (var v in new[] { -1.0, -0.5, 0.0, 0.5, 1.0 })
        {
            scale.AddMajor(v, v.ToString("+0.0;−0.0;0", CultureInfo.CurrentCulture));
        }

        bar.Axis.TickGenerator = scale;
        p.Axes.Color(bar.Axis, Palette("Ink2Color"));
        bar.Axis.TickLabelStyle.FontName = "Inter";
        bar.Axis.TickLabelStyle.FontSize = 10;
        heatLabels = h.Labels;
        heatValues = h.Values;
    }

    private void OnMouseMove(object sender, MouseEventArgs e)
    {
        var n = heatLabels.Count;
        if (n == 0)
        {
            return;
        }

        var at = view.Plot.GetCoordinates(view.GetCurrentPlotPixelPosition());
        int col = (int)Math.Round(at.X), row = n - 1 - (int)Math.Round(at.Y);
        ToolTip = row >= 0 && row < n && col >= 0 && col < n && heatValues[row][col] is { } v
            ? $"{heatLabels[row]} and {heatLabels[col]}: {v.ToString("+0.00;-0.00", CultureInfo.CurrentCulture)}"
            : null;
    }

    /// <summary>Red through the card colour to blue: negative, none, positive correlation.</summary>
    private sealed class DivergingColormap(ScottPlot.Color low, ScottPlot.Color mid, ScottPlot.Color high) : ScottPlot.IColormap
    {
        public string Name => "EQRisk diverging";

        public ScottPlot.Color GetColor(double position)
        {
            if (double.IsNaN(position))
            {
                return ScottPlot.Colors.Transparent;
            }

            var t = Math.Clamp(position, 0, 1);
            return t < 0.5 ? Mix(low, mid, t / 0.5) : Mix(mid, high, (t - 0.5) / 0.5);
        }

        private static ScottPlot.Color Mix(ScottPlot.Color a, ScottPlot.Color b, double t) => new(
            (byte)Math.Round(a.R + (b.R - a.R) * t), (byte)Math.Round(a.G + (b.G - a.G) * t),
            (byte)Math.Round(a.B + (b.B - a.B) * t));
    }
}
