using System.Globalization;

namespace EQRisk.Core.Jobs;

/// <summary>What kind of work a job does; drives icons, grouping and whether it can wait.</summary>
public enum JobKind
{
    DailyUpdate,
    Reestimate,
    Ingest,
    History,
    Overrides,
    Staging,
    ModelRebuild,
    Validation,
    Doctor,
    ExportSite,
    Snapshot,
    Compaction,
}

/// <summary>Where a re-estimate takes its data from.</summary>
public enum DataSource
{
    /// <summary>Download the session again, restage and re-estimate.</summary>
    FreshDownload,

    /// <summary>Use the raw data on disk: restage and re-estimate.</summary>
    StoredData,

    /// <summary>Use the staged tables as they are: re-estimate only (fastest).</summary>
    StoredStaging,
}

/// <summary>One <c>eqrisk</c> command, ready to run. <paramref name="NeedsKeys"/> is true for commands
/// that call a data vendor; only those receive API keys in their environment.</summary>
public sealed record JobSpec(JobKind Kind, string Label, IReadOnlyList<string> Arguments, bool NeedsKeys)
{
    public string ArgumentText => string.Join(' ', Arguments);

    public string CommandLine => "eqrisk " + ArgumentText;
}

/// <summary>
/// Every engine command the app can start, built in one place so the exact arguments are tested.
/// The flags are those of eqrisk/cli.py; tests/EQRisk.Core.Tests checks each command line.
/// </summary>
public static class EngineJobs
{
    public static JobSpec DailyUpdate() =>
        new(JobKind.DailyUpdate, "Daily update", ["run-daily"], NeedsKeys: true);

    /// <summary>The daily update from raw data already on disk: no downloads, so no keys.</summary>
    public static JobSpec DailyUpdateFromStoredData() =>
        new(JobKind.DailyUpdate, "Daily update (stored data)", ["run-daily", "--offline"], NeedsKeys: false);

    public static JobSpec Reestimate(DateOnly session, DataSource source)
    {
        List<string> args = ["run-daily", "--date", Iso(session), "--force"];
        if (source != DataSource.FreshDownload)
        {
            args.Add("--offline");
        }

        if (source == DataSource.StoredStaging)
        {
            args.Add("--no-stage");
        }

        return new(JobKind.Reestimate, $"Re-estimate {Iso(session)}", args, source == DataSource.FreshDownload);
    }

    public static JobSpec DownloadSession(DateOnly session) =>
        new(JobKind.Ingest, $"Download {Iso(session)}", ["ingest", "--date", Iso(session)], NeedsKeys: true);

    public static JobSpec DownloadHistory(DateOnly start, DateOnly end)
    {
        Ordered(start, end);
        return new(JobKind.History, $"Download history {Iso(start)} to {Iso(end)}",
            ["backfill", "--stage", "ingest", "--start", Iso(start), "--end", Iso(end)], NeedsKeys: true);
    }

    public static JobSpec PullOverrides() =>
        new(JobKind.Overrides, "Download override targets", ["pull-overrides"], NeedsKeys: true);

    public static JobSpec RebuildStaging(DateOnly through) =>
        new(JobKind.Staging, $"Rebuild staging through {Iso(through)}",
            ["backfill", "--stage", "stage", "--end", Iso(through)], NeedsKeys: false);

    public static JobSpec RebuildModel(DateOnly through) =>
        new(JobKind.ModelRebuild, $"Rebuild model history through {Iso(through)}",
            ["backfill", "--stage", "model", "--end", Iso(through)], NeedsKeys: false);

    public static JobSpec Validate(DateOnly start, DateOnly end)
    {
        Ordered(start, end);
        return new(JobKind.Validation, $"Validate {Iso(start)} to {Iso(end)}",
            ["validate", "--start", Iso(start), "--end", Iso(end)], NeedsKeys: false);
    }

    /// <summary>Connectivity and entitlement checks against every vendor, as JSON on stdout.</summary>
    public static JobSpec TestConnections() =>
        new(JobKind.Doctor, "Test connections", ["doctor", "--json"], NeedsKeys: true);

    public static JobSpec ExportViewer(DateOnly? asOf = null) =>
        asOf is { } d
            ? new(JobKind.ExportSite, $"Export viewer for {Iso(d)}", ["export-site", "--date", Iso(d)], NeedsKeys: false)
            : new(JobKind.ExportSite, "Export viewer", ["export-site"], NeedsKeys: false);

    public static JobSpec WriteSnapshot(DateOnly asOf, string outPath)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(outPath);
        return new(JobKind.Snapshot, $"Snapshot {Iso(asOf)}",
            ["snapshot", "--date", Iso(asOf), "--out", outPath], NeedsKeys: false);
    }

    public static JobSpec CompactMonth(int year, int month)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(month, 1);
        ArgumentOutOfRangeException.ThrowIfGreaterThan(month, 12);
        var ym = string.Create(CultureInfo.InvariantCulture, $"{year:D4}-{month:D2}");
        return new(JobKind.Compaction, $"Compact {ym}", ["compact", "--month", ym], NeedsKeys: false);
    }

    public static string Iso(DateOnly d) => d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);

    private static void Ordered(DateOnly start, DateOnly end)
    {
        if (start > end)
        {
            throw new ArgumentException($"start {Iso(start)} is after end {Iso(end)}", nameof(start));
        }
    }
}
