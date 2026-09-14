using EQRisk.Presentation.Charts;

namespace EQRisk.Presentation.Pages;

// Small display records shared by the pages. Each carries text already formatted and a tone, so
// the views only lay things out.

/// <summary>One figure in a page's stat rail.</summary>
public sealed record Stat(string Label, string Value, Tone Tone = Tone.Ink, string? Hint = null);

/// <summary>A quality gate of a daily run.</summary>
public sealed record GateRow(string Gate, string Result, Tone Tone, string Detail);

/// <summary>One line of a two-column table (a setting, a watermark).</summary>
public sealed record KeyValueRow(string Name, string Value);

/// <summary>A PASS / FAIL line of the validation scorecard.</summary>
public sealed record ScoreLine(string Area, string Criterion, string Value, string Status, Tone Tone);

/// <summary>A factor's return on one session.</summary>
public sealed record FactorReturnRow(string Factor, string Group, string Return, string TStat, string Sigmas, Tone Tone);

internal static class Tones
{
    public static Tone Sign(double? v) => v is null ? Tone.Muted : v >= 0 ? Tone.Bull : Tone.Bear;

    public static Tone Status(string status) => status.ToUpperInvariant() switch
    {
        "PASS" or "OK" or "SUCCEEDED" or "PRESENT" => Tone.Bull,
        "FAIL" or "FAILED" or "QUARANTINED" => Tone.Bear,
        "WARN" or "SKIPPED" or "STOPPED" or "INTERRUPTED" => Tone.Warn,
        _ => Tone.Muted,
    };
}
