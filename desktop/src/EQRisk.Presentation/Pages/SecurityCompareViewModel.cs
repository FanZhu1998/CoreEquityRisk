using System.Data;
using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using EQRisk.Core.Feed;

namespace EQRisk.Presentation.Pages;

/// <summary>
/// Factor exposures for a handful of securities at once: factors are rows, securities are columns.
///
/// It is a component, not a page. It owns only its own state and reads one engine method, so any page
/// that can name a ticker can host it without a Composition entry. The number of securities it will
/// hold is the engine's cap, read from every answer rather than written down here, so raising it in
/// `eqrisk/pipeline/feed.py` raises it in the app with no change on this side.
/// </summary>
public sealed partial class SecurityCompareViewModel(IEngineFeed feed) : ObservableObject
{
    /// <summary>Only until the engine has answered once and told us its own cap.</summary>
    private const int FallbackMax = 5;

    private readonly List<string> chosen = [];
    private DateOnly? lastAsOf;

    [ObservableProperty]
    public partial int Max { get; set; } = FallbackMax;

    /// <summary>Factors down, securities across. Null until a security is chosen.</summary>
    [ObservableProperty]
    public partial DataView? Table { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<SecuritySummary> Securities { get; set; } = [];

    [ObservableProperty]
    public partial string Summary { get; set; } = "";

    [ObservableProperty]
    public partial string? Problem { get; set; }

    [ObservableProperty]
    public partial bool IsOpen { get; set; }

    /// <summary>Industries every chosen name scores zero on are hidden unless this is set.</summary>
    [ObservableProperty]
    public partial bool ShowEveryFactor { get; set; }

    public IReadOnlyList<string> Chosen => chosen;

    public bool IsFull => chosen.Count >= Max;

    /// <summary>Add a name. Already-shown names are kept, and a full panel reports rather than drops.</summary>
    public async Task<bool> AddAsync(string? ticker, DateOnly? asOf, CancellationToken ct = default)
    {
        var symbol = (ticker ?? "").Trim().ToUpperInvariant();
        if (symbol.Length == 0)
        {
            return false;
        }

        if (chosen.Contains(symbol, StringComparer.Ordinal))
        {
            IsOpen = true;
            return true;
        }

        if (IsFull)
        {
            Problem = $"Up to {Max} securities can be compared at once. Remove one first.";
            IsOpen = true;
            return false;
        }

        chosen.Add(symbol);
        await ReloadAsync(asOf, ct).ConfigureAwait(false);
        return true;
    }

    public async Task RemoveAsync(string? ticker, DateOnly? asOf, CancellationToken ct = default)
    {
        chosen.RemoveAll(t => string.Equals(t, ticker, StringComparison.OrdinalIgnoreCase));
        if (chosen.Count == 0)
        {
            Close();
            return;
        }

        await ReloadAsync(asOf, ct).ConfigureAwait(false);
    }

    public void Close()
    {
        chosen.Clear();
        Table = null;
        Securities = [];
        Summary = "";
        Problem = null;
        IsOpen = false;
    }

    /// <summary>Re-read the chosen names, after the page's as-of date moved or a job landed.</summary>
    public async Task ReloadAsync(DateOnly? asOf, CancellationToken ct = default)
    {
        if (chosen.Count == 0)
        {
            Close();
            return;
        }

        lastAsOf = asOf;
        try
        {
            var info = await feed.SecurityExposuresAsync(chosen, asOf, ct).ConfigureAwait(false);
            Max = info.Max > 0 ? info.Max : FallbackMax;
            Apply(info);
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            Problem = ex.Message;
        }

        IsOpen = true;
    }

    private void Apply(SecurityExposuresInfo info)
    {
        // The engine answers only for names it carries on that date; keep our list in step with it.
        var kept = info.Securities.Select(s => s.Ticker).ToList();
        chosen.RemoveAll(t => !kept.Contains(t, StringComparer.Ordinal));
        Problem = info.Unmatched.Count == 0 ? null
            : $"No model row on this date for {string.Join(", ", info.Unmatched)}.";

        if (chosen.Count == 0)
        {
            var note = Problem;
            Close();
            Problem = note;
            return;
        }

        var shown = info.Factors.Where(f => ShowEveryFactor || f.Group != "industry" || HasValue(f)).ToList();
        using var table = new DataTable("compare") { Locale = CultureInfo.InvariantCulture };
        table.Columns.Add("Group", typeof(string));
        table.Columns.Add("Factor", typeof(string));
        foreach (var s in info.Securities)
        {
            table.Columns.Add(s.Ticker, typeof(double));
        }

        foreach (var row in shown)
        {
            var values = new object?[2 + info.Securities.Count];
            values[0] = row.Group;
            values[1] = Fmt.Factor(row.Factor);
            for (var i = 0; i < info.Securities.Count; i++)
            {
                values[2 + i] = i < row.Values.Count && row.Values[i] is { } v ? v : DBNull.Value;
            }

            table.Rows.Add(values);
        }

        Table = table.DefaultView;
        Securities = info.Securities;
        var hidden = info.Factors.Count - shown.Count;
        Summary = $"{Fmt.Plural(info.Securities.Count, "security", "securities")} on {info.AsOf:yyyy-MM-dd} · " +
                  $"{shown.Count} of {info.Factors.Count} factors" +
                  (hidden > 0 ? $" · {hidden} all-zero industries hidden" : "");
    }

    private static bool HasValue(SecurityFactorRow row) =>
        row.Values.Any(v => v is { } x && Math.Abs(x) > 1e-12);

    /// <summary>Toggling the filter re-reads at the date the panel last used, so the host need not help.</summary>
    partial void OnShowEveryFactorChanged(bool value) => _ = ReloadAsync(lastAsOf);
}
