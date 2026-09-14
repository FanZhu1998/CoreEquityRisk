using EQRisk.Core.Feed;

namespace EQRisk.Presentation.Activity;

/// <summary>How the model stands, in one word, for the tray icon's colour.</summary>
public enum Health
{
    Unknown,
    UpToDate,
    Pending,
    Problem,
    Running,
}

/// <summary>A one-line reading of the model's state, for the tray icon and its flyout.</summary>
public sealed record StatusView(Health Health, string Headline, string Detail);

public static class StatusSummary
{
    /// <summary>Quarantined or failed beats pending beats up to date: the worst news shows first.</summary>
    public static StatusView From(ModelStatus status)
    {
        ArgumentNullException.ThrowIfNull(status);
        var run = status.LastRun;
        if (run is { Status: "FAILED" or "QUARANTINED" } bad)
        {
            var gates = bad.FailGates.Count > 0 ? $" ({string.Join(", ", bad.FailGates)})" : "";
            return new StatusView(Health.Problem, $"The last update {bad.Status.ToLowerInvariant()}",
                $"{Fmt.Date(bad.AsOf)}{gates}. Published model: {Fmt.Date(status.LatestGood)}.");
        }

        if (!status.UpToDate)
        {
            return new StatusView(Health.Pending, $"{Fmt.Plural(status.Pending.Count, "session", "sessions")} to estimate",
                $"Model as of {Fmt.Date(status.Last)}; next {Fmt.Date(status.Pending[0])}.");
        }

        return new StatusView(Health.UpToDate, $"Up to date through {Fmt.Date(status.Last)}",
            $"Next session {Fmt.Date(status.NextSession)}, after {status.ReadyAfterEt} ET.");
    }

    public static StatusView Running(string label, string step) =>
        new(Health.Running, label, step.Length > 0 ? $"Now: {step}." : "Running.");

    public static StatusView Unavailable(string why) => new(Health.Unknown, "Model status unknown", why);
}
