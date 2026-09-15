using System.Collections.ObjectModel;
using System.Text.Json;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Pages;

/// <summary>A key on the Data page: where it comes from, never what it is.</summary>
public sealed record KeyRow(string Env, string Purpose, string Required, string Source, Tone Tone, bool InVault);

/// <summary>One line of `eqrisk doctor`: a source checked, how it went, and the detail.</summary>
public sealed record DoctorRow(string Check, string Status, Tone Tone, string Detail);

public sealed record ExceptionCountRow(string Area, string Issue, int Count);

/// <summary>
/// Data: which keys are configured (from the Windows vault or the engine's .env, values never
/// shown), connection checks, what is on disk, loading data, and staging's data-quality exceptions.
/// </summary>
public sealed partial class DataViewModel : PageViewModel
{
    private const int ExceptionRows = 5000;

    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly IKeyVault vault;
    private readonly IDialogs dialogs;
    private readonly IJobRunner runner;
    private IReadOnlyList<ExceptionRow> allExceptions = [];

    public DataViewModel(IEngineFeed feed, JobLauncher launcher, IKeyVault vault, IDialogs dialogs, IJobRunner runner,
        IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.launcher = launcher;
        this.vault = vault;
        this.dialogs = dialogs;
        this.runner = runner;
        Keys = [];
        Doctor = [];
        Stats = [];
        References = [];
        Watermarks = [];
        ExceptionCounts = [];
        Exceptions = [];
        ExceptionsNote = "";
        var today = DateTime.Today;
        SessionDate = today.AddDays(-1);
        RangeEnd = today;
        RangeStart = today.AddYears(-1);
    }

    public override string Key => "data";

    public override string Title => "Data";

    public override string Eyebrow => "02 — Data";

    public override string Heading => "Data &";

    public override string HeadingAccent => "connections";

    public override string? Lede =>
        "Prices come from EODHD, filings from SEC EDGAR, the risk-free rate from FRED and index membership from the " +
        "public fja05680 history. Keys are kept encrypted for your Windows account, or read from the engine's .env; " +
        "this app only reports where each one is set and never shows it.";

    [ObservableProperty]
    public partial IReadOnlyList<KeyRow> Keys { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<DoctorRow> Doctor { get; set; }

    [ObservableProperty]
    public partial string? DoctorNote { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<Stat> Stats { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<KeyValueRow> References { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<KeyValueRow> Watermarks { get; set; }

    [ObservableProperty]
    public partial DateTime? SessionDate { get; set; }

    [ObservableProperty]
    public partial DateTime? RangeStart { get; set; }

    [ObservableProperty]
    public partial DateTime? RangeEnd { get; set; }

    [ObservableProperty]
    public partial DateOnly? RawThrough { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<ExceptionCountRow> ExceptionCounts { get; set; }

    /// <summary>Issues chosen to list; empty lists them all.</summary>
    public ObservableCollection<string> SelectedIssues { get; } = [];

    [ObservableProperty]
    public partial IReadOnlyList<ExceptionRow> Exceptions { get; set; }

    [ObservableProperty]
    public partial string ExceptionsNote { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var dotEnv = await feed.KeysAsync(ct);
        var inventory = await feed.InventoryAsync(ct);
        var exceptions = await feed.ExceptionsAsync(ExceptionRows, ct);
        ShowKeys(dotEnv);
        RawThrough = inventory.EodLast;
        Stats =
        [
            new("Price sessions", Fmt.Count(inventory.EodSessions)),
            new("Prices from / to", $"{Fmt.Date(inventory.EodFirst)} → {Fmt.Date(inventory.EodLast)}"),
            new("Companies with SEC filings", Fmt.Count(inventory.EdgarCompanies)),
            new("Staged through", Fmt.Date(inventory.StagedLast)),
            new("Data folder", Fmt.Bytes(inventory.DataBytes), Tone.Ink, inventory.DataDir),
        ];
        References = inventory.Refs.Select(r => new KeyValueRow($"{r.Dataset} ({r.Source})", Fmt.Date(r.Snapshot))).ToList();
        Watermarks = inventory.Watermarks.OrderBy(w => w.Key, StringComparer.Ordinal)
            .Select(w => new KeyValueRow(w.Key, w.Value.ValueKind == JsonValueKind.String ? w.Value.GetString() ?? "" : w.Value.ToString()))
            .ToList();
        ExceptionCounts = exceptions.Counts.Select(c => new ExceptionCountRow(c.Area ?? "", c.Issue, c.Count)).ToList();
        allExceptions = exceptions.Rows;
        FilterExceptions();
        ExceptionsNote = exceptions.Total == 0
            ? "No data-quality exceptions."
            : $"{Fmt.Plural(exceptions.Total, "exception", "exceptions")}. Most are fixed by a row in configs/overrides/ " +
              "(DECISIONS D-008, D-011), then Rebuild staging.";
    }

    protected override void OnBusyChanged()
    {
        TestConnectionsCommand.NotifyCanExecuteChanged();
        DownloadSessionCommand.NotifyCanExecuteChanged();
        DownloadHistoryCommand.NotifyCanExecuteChanged();
        PullOverridesCommand.NotifyCanExecuteChanged();
        RebuildStagingCommand.NotifyCanExecuteChanged();
    }

    protected override void OnJobFinished(JobRecord job)
    {
        if (job.Kind == JobKind.Doctor)
        {
            _ = ReadDoctorAsync(job);
        }
    }

    [RelayCommand]
    public void FilterExceptions()
    {
        var chosen = SelectedIssues.ToHashSet(StringComparer.Ordinal);
        Exceptions = chosen.Count == 0 ? allExceptions : allExceptions.Where(e => chosen.Contains(e.Issue)).ToList();
    }

    [RelayCommand]
    private async Task SetKeyAsync(KeyRow row)
    {
        ArgumentNullException.ThrowIfNull(row);
        var value = await dialogs.AskSecretAsync($"Set {row.Env}",
            $"{row.Purpose}. It is encrypted for your Windows account, never shown again, and reaches only jobs " +
            "that download data. It takes the place of any value in .env.");
        if (string.IsNullOrWhiteSpace(value))
        {
            return;
        }

        vault.Store(row.Env, value);
        Messenger.Send(new KeysChangedMessage());
        await ReloadAsync();
    }

    [RelayCommand]
    private async Task RemoveKeyAsync(KeyRow row)
    {
        ArgumentNullException.ThrowIfNull(row);
        if (!await dialogs.ConfirmAsync($"Remove {row.Env} from the vault?",
                "Jobs will fall back to the value in the engine's .env file, if there is one.", "Remove", ConfirmTone.Destructive))
        {
            return;
        }

        vault.Remove(row.Env);
        Messenger.Send(new KeysChangedMessage());
        await ReloadAsync();
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task TestConnectionsAsync()
    {
        Doctor = [];
        DoctorNote = "Checking every source: about twenty seconds.";
        if (!await launcher.RunAsync(EngineJobs.TestConnections()))
        {
            DoctorNote = null;
        }
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private Task DownloadSessionAsync() =>
        SessionDate is { } d ? launcher.RunAsync(EngineJobs.DownloadSession(DateOnly.FromDateTime(d))) : Task.CompletedTask;

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task DownloadHistoryAsync()
    {
        if (RangeStart is not { } a || RangeEnd is not { } b)
        {
            return;
        }

        if (a > b)
        {
            await dialogs.ShowErrorAsync("Check the dates", "The start is after the end.");
            return;
        }

        await launcher.RunAsync(EngineJobs.DownloadHistory(DateOnly.FromDateTime(a), DateOnly.FromDateTime(b)));
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task PullOverridesAsync() => await launcher.RunAsync(EngineJobs.PullOverrides());

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task RebuildStagingAsync() =>
        await launcher.RunAsync(EngineJobs.RebuildStaging(RawThrough ?? DateOnly.FromDateTime(DateTime.Today)));

    private bool Idle() => !launcher.IsBusy;

    private void ShowKeys(IReadOnlyList<KeyPresence> dotEnv)
    {
        var inVault = vault.Names;
        var inDotEnv = dotEnv.Where(k => k.Present).Select(k => k.Env).ToHashSet(StringComparer.Ordinal);
        Keys = KeyCatalog.Resolve(inVault, inDotEnv).Select(s => new KeyRow(
            s.Definition.Env, s.Definition.Purpose, s.Definition.Required ? "required" : "optional",
            s.Source switch
            {
                KeySource.Vault => "Windows vault (encrypted)",
                KeySource.DotEnv => ".env file",
                _ => "missing",
            },
            s.Source == KeySource.Missing ? (s.Definition.Required ? Tone.Bear : Tone.Muted) : Tone.Bull,
            s.Source == KeySource.Vault)).ToList();
    }

    private async Task ReadDoctorAsync(JobRecord job)
    {
        var text = await runner.ReadLogAsync(job, 256 * 1024);
        var json = text.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.StartsWith('[') && l.EndsWith(']'));
        if (json is null)
        {
            DoctorNote = "The check did not finish; its log is on the Jobs page.";
            return;
        }

        try
        {
            var rows = JsonSerializer.Deserialize<List<Dictionary<string, string>>>(json) ?? [];
            Doctor = rows
                .Where(r => !r.GetValueOrDefault("check", "").StartsWith("env ", StringComparison.Ordinal))
                .Select(r => new DoctorRow(r.GetValueOrDefault("check", ""), r.GetValueOrDefault("status", ""),
                    r.GetValueOrDefault("status", "") switch
                    {
                        "ok" => Tone.Bull,
                        "fail" => Tone.Bear,
                        "warn" => Tone.Warn,
                        _ => Tone.Muted,
                    }, r.GetValueOrDefault("detail", "")))
                .ToList();
            DoctorNote = null;
        }
        catch (JsonException)
        {
            DoctorNote = "The check's output could not be read; its log is on the Jobs page.";
        }
    }
}
