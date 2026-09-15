using System.Collections;
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using EQRisk.Core.Jobs;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Pages;

namespace EQRisk.Desktop.Views;

/// <summary>A palette tone as a brush; with <see cref="Dim"/>, the tinted background behind it.</summary>
public sealed class ToneBrushConverter : IValueConverter
{
    public bool Dim { get; set; }

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var tone = value switch
        {
            Tone t => t,
            JobStatus s => s switch
            {
                JobStatus.Succeeded => Tone.Bull,
                JobStatus.Failed => Tone.Bear,
                JobStatus.Running => Tone.Accent,
                _ => Tone.Warn,
            },
            _ => Tone.Muted,
        };
        var key = (tone, Dim) switch
        {
            (Tone.Bull, false) => "BullBrush",
            (Tone.Bear, false) => "BearBrush",
            (Tone.Warn, false) => "WarnBrush",
            (Tone.Muted, false) => "MutedBrush",
            (Tone.Ink, false) => "InkBrush",
            (Tone.AccentSoft, false) => "AccentHiBrush",
            (_, false) => "AccentBrush",
            (Tone.Bull, true) => "BullDimBrush",
            (Tone.Bear, true) => "BearDimBrush",
            (Tone.Warn, true) => "WarnDimBrush",
            (Tone.Muted or Tone.Ink, true) => "MutedDimBrush",
            (_, true) => "AccentDimBrush",
        };
        return Application.Current.FindResource(key);
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>Visible when the value is "something": true, a non-empty string or list, a non-zero
/// number, any other object. <see cref="Invert"/> flips it.</summary>
public sealed class VisibleWhenConverter : IValueConverter
{
    public bool Invert { get; set; }

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var shown = value switch
        {
            null => false,
            bool b => b,
            string s => s.Length > 0,
            int i => i != 0,
            ICollection c => c.Count > 0,
            _ => true,
        };
        return shown ^ Invert ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

public sealed class NotConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) => value is not true;

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) => value is not true;
}

public sealed class UpperConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        (value as string)?.ToUpper(CultureInfo.CurrentCulture) ?? "";

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>A window length in years, in words: "1 year", "3 years".</summary>
public sealed class YearsTextConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) => value switch
    {
        1 => "1 year",
        int n => n.ToString(culture) + " years",
        _ => "",
    };

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>A re-estimate's data source, in words.</summary>
public sealed class DataSourceTextConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is DataSource s ? EstimateViewModel.Describe(s) : "";

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>A page's icon in the navigation rail (Segoe Fluent Icons).</summary>
public sealed class PageGlyphConverter : IValueConverter
{
    private static readonly Dictionary<string, string> Glyphs = new(StringComparer.Ordinal)
    {
        ["today"] = "",
        ["data"] = "",
        ["estimate"] = "",
        ["validate"] = "",
        ["factor-returns"] = "",
        ["factor-risk"] = "",
        ["exposures"] = "",
        ["specific-risk"] = "",
        ["portfolio"] = "",
        ["optimizer"] = "",
        ["jobs"] = "",
        ["publish"] = "",
        ["settings"] = "",
    };

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        value is string key && Glyphs.TryGetValue(key, out var glyph) ? glyph : "";

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>Colours a number red or green by sign, for signed figures in tables.</summary>
public sealed class SignBrushConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture) =>
        Application.Current.FindResource(value switch
        {
            double d when d > 0 => "BullBrush",
            double d when d < 0 => "BearBrush",
            _ => "InkBrush",
        });

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture) =>
        throw new NotSupportedException();
}

/// <summary>Frozen brushes for code that draws (charts, the tray icon), looked up once.</summary>
internal static class PaletteColors
{
    public static Color Get(string key) =>
        Application.Current?.TryFindResource(key) is Color c ? c : Colors.Gray;
}
