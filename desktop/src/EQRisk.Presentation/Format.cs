using System.Globalization;

namespace EQRisk.Presentation;

/// <summary>How numbers, dates and names read on screen. A missing value is a dash, never a zero.</summary>
public static class Fmt
{
    public const string Dash = "—";

    private static CultureInfo Culture => CultureInfo.CurrentCulture;

    public static string Pct(double? value, int decimals = 1) =>
        value is { } v && double.IsFinite(v) ? (v * 100).ToString("F" + decimals, Culture) + "%" : Dash;

    public static string SignedPct(double? value, int decimals = 2) =>
        value is { } v && double.IsFinite(v) ? (v >= 0 ? "+" : "") + Pct(v, decimals) : Dash;

    public static string Num(double? value, string format = "0.00") =>
        value is { } v && double.IsFinite(v) ? v.ToString(format, Culture) : Dash;

    public static string WithSign(double? value, string format = "0.00") =>
        value is { } v && double.IsFinite(v) ? (v >= 0 ? "+" : "") + v.ToString(format, Culture) : Dash;

    public static string Count(long? value) => value is { } v ? v.ToString("N0", Culture) : Dash;

    public static string Date(DateOnly? date) =>
        date?.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) ?? Dash;

    public static string Time(DateTimeOffset? at) =>
        at?.ToLocalTime().ToString("yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture) ?? Dash;

    public static string Bytes(long bytes) => bytes switch
    {
        >= 1_000_000_000 => (bytes / 1e9).ToString("0.0", Culture) + " GB",
        >= 1_000_000 => (bytes / 1e6).ToString("0", Culture) + " MB",
        _ => (bytes / 1e3).ToString("0", Culture) + " KB",
    };

    public static string Duration(TimeSpan? span)
    {
        if (span is not { } t)
        {
            return Dash;
        }

        var s = (int)Math.Max(0, t.TotalSeconds);
        return s >= 3600 ? $"{s / 3600} h {s % 3600 / 60:00} min"
            : s >= 60 ? $"{s / 60} min {s % 60:00} s"
            : $"{s} s";
    }

    /// <summary>A factor code as words: CONSUMER_DURABLES_APPAREL reads "Consumer durables apparel".</summary>
    public static string Factor(string code)
    {
        ArgumentNullException.ThrowIfNull(code);
        if (code.Length == 0)
        {
            return code;
        }

        var words = code.Replace('_', ' ').ToLowerInvariant();
        return char.ToUpperInvariant(words[0]) + words[1..];
    }

    public static string Plural(int count, string one, string many) =>
        count.ToString("N0", Culture) + " " + (count == 1 ? one : many);
}
