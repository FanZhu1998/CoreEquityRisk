using System.Globalization;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Engine;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Core.Settings;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Charts;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Pages;

public sealed record JobLine(
    JobRecord Record, string Started, string Label, string Status, Tone Tone, string Duration, string Trigger, string Command);

public sealed record RunLine(string Started, string Command, string Session, string Status, Tone Tone, string Gates, string Minutes);

/// <summary>Jobs: every job this app has run (with its log) and every pipeline run manifest.</summary>
public sealed partial class JobsViewModel : PageViewModel
{
    private const int JobsShown = 200;
    private const int RunsShown = 100;
    private const int LogChars = 400_000;

    private readonly IJobStore store;
    private readonly IJobRunner runner;
    private readonly IEngineFeed feed;
    private readonly IShell shell;
    private readonly IAppInfo app;

    public JobsViewModel(IJobStore store, IJobRunner runner, IEngineFeed feed, IShell shell, IAppInfo app, IMessenger messenger)
        : base(messenger)
    {
        this.store = store;
        this.runner = runner;
        this.feed = feed;
        this.shell = shell;
        this.app = app;
        Jobs = [];
        Runs = [];
        Log = "";
    }

    public override string Key => "jobs";

    public override string Title => "Jobs & runs";

    public override string Eyebrow => "System — Jobs";

    public override string Heading => "Jobs &";

    public override string HeadingAccent => "run history";

    public override string? Lede =>
        "Every job this app has started, with its full log, and every pipeline run manifest the engine wrote: command, " +
        "session, status, gates and timings, with the config hash and git revision for reproduction.";

    [ObservableProperty]
    public partial IReadOnlyList<JobLine> Jobs { get; set; }

    [ObservableProperty]
    public partial JobLine? SelectedJob { get; set; }

    [ObservableProperty]
    public partial string Log { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<RunLine> Runs { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var jobs = await store.RecentAsync(JobsShown, ct);
        Jobs = jobs.Select(j => new JobLine(j, Fmt.Time(j.StartedAt), j.Label, j.Status.ToString(), Tones.Status(j.Status.ToString()),
            j.IsRunning ? "running" : Fmt.Duration(j.Duration), j.Trigger.ToString(), j.CommandLine)).ToList();
        SelectedJob ??= Jobs.Count > 0 ? Jobs[0] : null;
        try
        {
            var runs = await feed.RunsAsync(RunsShown, ct);
            Runs = runs.Runs.Select(r => new RunLine(Fmt.Time(r.StartedAt), r.Command, Fmt.Date(r.AsOf), r.Status, Tones.Status(r.Status),
                r.FailGates.Count + r.WarnGates.Count == 0 ? "all pass" : string.Join(", ", r.FailGates.Concat(r.WarnGates)),
                r.Minutes is { } m ? m.ToString("0.0", CultureInfo.CurrentCulture) : Fmt.Dash)).ToList();
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            Runs = [];                                   // the app's own history still shows without the engine
        }
    }

    partial void OnSelectedJobChanged(JobLine? value) => _ = ShowLogAsync(value);

    [RelayCommand]
    private void OpenLogsFolder() => shell.OpenFolder(app.LogsDirectory);

    [RelayCommand]
    private void OpenLogFile()
    {
        if (SelectedJob is { } j && File.Exists(j.Record.LogPath))
        {
            shell.OpenFile(j.Record.LogPath);
        }
    }

    private async Task ShowLogAsync(JobLine? line)
    {
        Log = line is null ? "" : await runner.ReadLogAsync(line.Record, LogChars) is { Length: > 0 } text ? text : "(no output)";
    }
}

/// <summary>Publish: the static offline viewer, snapshot files for notebooks, and monthly compaction.</summary>
public sealed partial class PublishViewModel : PageViewModel
{
    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly IViewerServer viewer;
    private readonly IShell shell;
    private readonly IDialogs dialogs;

    public PublishViewModel(IEngineFeed feed, JobLauncher launcher, IViewerServer viewer, IShell shell, IDialogs dialogs,
        IMessenger messenger)
        : base(messenger)
    {
        this.feed = feed;
        this.launcher = launcher;
        this.viewer = viewer;
        this.shell = shell;
        this.dialogs = dialogs;
        SiteStatus = "";
        Snapshots = [];
        var previous = DateTime.Today.AddMonths(-1);
        CompactMonth = new DateTime(previous.Year, previous.Month, 1);
    }

    public override string Key => "publish";

    public override string Title => "Publish";

    public override string Eyebrow => "System — Publish";

    public override string Heading => "Publish &";

    public override string HeadingAccent => "export";

    public override string? Lede =>
        "Share results without this app: a self-contained viewer of model outputs (no vendor prices or fundamentals), " +
        "or a snapshot file of exposures, factor covariance and specific variances for notebooks and optimizers.";

    [ObservableProperty]
    public partial string SiteStatus { get; set; }

    [ObservableProperty]
    public partial bool SiteExported { get; set; }

    [ObservableProperty]
    public partial string? SiteFolder { get; set; }

    [ObservableProperty]
    public partial bool IsServing { get; set; }

    [ObservableProperty]
    public partial string? ServingAt { get; set; }

    [ObservableProperty]
    public partial DateTime? SnapshotDate { get; set; }

    [ObservableProperty]
    public partial string? ExportsFolder { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<KeyValueRow> Snapshots { get; set; }

    [ObservableProperty]
    public partial DateTime? CompactMonth { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var outputs = await feed.OutputsAsync(ct);
        var status = await feed.StatusAsync(ct);
        SiteFolder = outputs.Site.Path;
        SiteExported = outputs.Site.Exported;
        SiteStatus = outputs.Site.Exported
            ? $"{Fmt.Bytes(outputs.Site.Bytes)}, exported {Fmt.Time(outputs.Site.Modified)} to {outputs.Site.Path}"
            : $"Not exported yet. It will be written to {outputs.Site.Path}.";
        ExportsFolder = outputs.ExportsDir;
        Snapshots = outputs.Snapshots.Select(s => new KeyValueRow(s.Name, $"{Fmt.Bytes(s.Bytes)} · {Fmt.Time(s.Modified)}")).ToList();
        SnapshotDate ??= status.LatestGood?.ToDateTime(TimeOnly.MinValue);
        IsServing = viewer.IsServing;
        ServingAt = viewer.Url?.ToString();
    }

    protected override void OnBusyChanged()
    {
        ExportCommand.NotifyCanExecuteChanged();
        WriteSnapshotCommand.NotifyCanExecuteChanged();
        CompactCommand.NotifyCanExecuteChanged();
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private async Task ExportAsync() => await launcher.RunAsync(EngineJobs.ExportViewer());

    [RelayCommand]
    private async Task ServeAsync()
    {
        if (SiteFolder is null || !SiteExported)
        {
            return;
        }

        try
        {
            var url = await viewer.StartAsync(SiteFolder);
            IsServing = true;
            ServingAt = url.ToString();
            shell.OpenUrl(url);
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            await dialogs.ShowErrorAsync("The viewer could not be served", Problems.Describe(ex));
        }
    }

    [RelayCommand]
    private void StopServing()
    {
        viewer.StopServing();
        IsServing = false;
        ServingAt = null;
    }

    [RelayCommand]
    private void OpenSiteFolder()
    {
        if (SiteFolder is { } f && Directory.Exists(f))
        {
            shell.OpenFolder(f);
        }
    }

    [RelayCommand]
    private void OpenExportsFolder()
    {
        if (ExportsFolder is { } f)
        {
            Directory.CreateDirectory(f);
            shell.OpenFolder(f);
        }
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private Task WriteSnapshotAsync()
    {
        if (SnapshotDate is not { } d || ExportsFolder is not { } folder)
        {
            return Task.CompletedTask;
        }

        var day = DateOnly.FromDateTime(d);
        return launcher.RunAsync(EngineJobs.WriteSnapshot(day, Path.Combine(folder, $"snapshot_{EngineJobs.Iso(day)}.npz")));
    }

    [RelayCommand(CanExecute = nameof(Idle))]
    private Task CompactAsync() =>
        CompactMonth is { } m ? launcher.RunAsync(EngineJobs.CompactMonth(m.Year, m.Month)) : Task.CompletedTask;

    private bool Idle() => !launcher.IsBusy;
}

/// <summary>
/// Settings: the engine folder, the model's parameters, the daily schedule, how the app behaves in
/// the notification area, updates, and where everything lives.
/// </summary>
public sealed partial class SettingsViewModel : PageViewModel
{
    private readonly ISettingsStore settings;
    private readonly IEngineLocator locator;
    private readonly IEngineFeed feed;
    private readonly IScheduledTaskService schedule;
    private readonly IWindowsIntegration windows;
    private readonly IUpdateService updates;
    private readonly IKeyVault vault;
    private readonly IDialogs dialogs;
    private readonly IShell shell;
    private readonly IAppInfo app;
    private bool loadingSettings;

    public SettingsViewModel(ISettingsStore settings, IEngineLocator locator, IEngineFeed feed, IScheduledTaskService schedule,
        IWindowsIntegration windows, IUpdateService updates, IKeyVault vault, IDialogs dialogs, IShell shell, IAppInfo app,
        IMessenger messenger)
        : base(messenger)
    {
        this.settings = settings;
        this.locator = locator;
        this.feed = feed;
        this.schedule = schedule;
        this.windows = windows;
        this.updates = updates;
        this.vault = vault;
        this.dialogs = dialogs;
        this.shell = shell;
        this.app = app;
        EngineRoot = "";
        EngineCheckText = "";
        Model = [];
        ScheduleText = "";
        ScheduleTimes = Enumerable.Range(0, 48).Select(i => new TimeOnly(i / 2, i % 2 * 30).ToString("HH:mm", CultureInfo.InvariantCulture)).ToList();
        ScheduleTime = settings.Current.ScheduleTime.ToString("HH:mm", CultureInfo.InvariantCulture);
        UpdateText = "";
        Paths = [];
    }

    public override string Key => "settings";

    public override string Title => "Settings";

    public override string Eyebrow => "System — Settings";

    public override string Heading => "Engine, schedule &";

    public override string HeadingAccent => "settings";

    [ObservableProperty]
    public partial string EngineRoot { get; set; }

    [ObservableProperty]
    public partial string EngineCheckText { get; set; }

    [ObservableProperty]
    public partial bool EngineOk { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<KeyValueRow> Model { get; set; }

    public IReadOnlyList<string> ScheduleTimes { get; }

    [ObservableProperty]
    public partial string ScheduleTime { get; set; }

    [ObservableProperty]
    public partial string ScheduleText { get; set; }

    [ObservableProperty]
    public partial bool ScheduleRegistered { get; set; }

    [ObservableProperty]
    public partial bool CloseToTray { get; set; }

    [ObservableProperty]
    public partial bool StartWithWindows { get; set; }

    [ObservableProperty]
    public partial bool EfficiencyMode { get; set; }

    [ObservableProperty]
    public partial bool NotifyOnFinish { get; set; }

    [ObservableProperty]
    public partial bool CheckForUpdates { get; set; }

    public bool CanUpdate => updates.CanUpdate;

    public bool NeedsShortcut => !app.IsInstalled;

    [ObservableProperty]
    public partial string UpdateText { get; set; }

    [ObservableProperty]
    public partial bool HasUpdateToken { get; set; }

    [ObservableProperty]
    public partial IReadOnlyList<KeyValueRow> Paths { get; set; }

    protected override async Task LoadAsync(CancellationToken ct)
    {
        var s = settings.Current;
        loadingSettings = true;
        CloseToTray = s.CloseToTray;
        StartWithWindows = windows.StartsWithWindows;
        EfficiencyMode = s.EfficiencyModeWhenHidden;
        NotifyOnFinish = s.NotifyOnFinish;
        CheckForUpdates = s.CheckForUpdates;
        loadingSettings = false;
        HasUpdateToken = vault.Names.Contains(KeyCatalog.UpdateToken.Env);
        var located = locator.Locate(s.EngineRoot, app.AppDirectory);
        EngineRoot = located.Engine?.Root ?? s.EngineRoot ?? "";
        ShowCheck(located);
        Paths =
        [
            new("App version", app.Version),
            new("App data (settings, vault, job history)", app.DataDirectory),
            new("App logs", app.LogsDirectory),
            new("Installed with Setup.exe", app.IsInstalled ? "yes: updates from GitHub Releases" : "no: a portable or development copy"),
        ];
        await ShowScheduleAsync(ct);
        try
        {
            var c = await feed.ConfigAsync(ct);
            var fc = c.FactorCov;
            Model =
            [
                new("Model", $"{c.ModelId} (preset {c.Preset}, horizon {c.HorizonDays} sessions)"),
                new("Config hash", c.ConfigHash),
                new("Factor covariance", string.Create(CultureInfo.InvariantCulture,
                    $"volatility half-life {fc.VolHalfLife} / NW {fc.VolNwLags}, correlation half-life {fc.CorrHalfLife} / NW {fc.CorrNwLags}, eigen a = {fc.EigenA}, VRA half-life {fc.VraHalfLife}")),
                new("Specific risk", string.Create(CultureInfo.InvariantCulture,
                    $"half-life {c.SpecificRisk.HalfLife}, shrinkage q = {c.SpecificRisk.ShrinkageQ}")),
                new("Data sources", $"prices {c.Sources.Prices}, fundamentals {c.Sources.Fundamentals}, membership {c.Sources.Membership}, risk-free {c.Sources.RiskFree}"),
                new("Configuration file", c.Paths.Config),
                new("Data folder", c.Paths.Data),
                new("Reports folder", c.Paths.Reports),
            ];
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            Model = [new("Model", Problems.Describe(ex))];
        }
    }

    partial void OnCloseToTrayChanged(bool value) => Persist(s => s with { CloseToTray = value });

    partial void OnEfficiencyModeChanged(bool value) => Persist(s => s with { EfficiencyModeWhenHidden = value });

    partial void OnNotifyOnFinishChanged(bool value) => Persist(s => s with { NotifyOnFinish = value });

    partial void OnCheckForUpdatesChanged(bool value) => Persist(s => s with { CheckForUpdates = value });

    partial void OnStartWithWindowsChanged(bool value)
    {
        if (!loadingSettings && value != windows.StartsWithWindows)
        {
            windows.SetStartWithWindows(value);
            Persist(s => s with { StartWithWindows = value });
        }
    }

    [RelayCommand]
    private async Task BrowseEngineAsync()
    {
        if (dialogs.PickFolder("Choose the CoreEquityRisk folder", EngineRoot) is not { } folder)
        {
            return;
        }

        var check = locator.Check(folder);
        ShowCheck(check);
        if (check.Ok)
        {
            EngineRoot = check.Engine!.Root;
            await settings.SaveAsync(settings.Current with { EngineRoot = EngineRoot });   // the engine restarts from here
        }
    }

    [RelayCommand]
    private async Task RegisterScheduleAsync()
    {
        var at = TimeOnly.ParseExact(ScheduleTime, "HH:mm", CultureInfo.InvariantCulture);
        if (!await dialogs.ConfirmAsync("Schedule the daily update?",
                $"Windows Task Scheduler will run the daily update every day at {ScheduleTime}, and as soon as the PC is " +
                "on again if it missed that time. It runs in the background under your account and uses the same keys.",
                "Schedule"))
        {
            return;
        }

        await schedule.RegisterAsync(at, app.LauncherPath, "--run-daily", app.AppDirectory);
        await settings.SaveAsync(settings.Current with { ScheduleTime = at });
        await ShowScheduleAsync(CancellationToken.None);
    }

    [RelayCommand]
    private async Task RunScheduleNowAsync()
    {
        await schedule.RunNowAsync();
        await ShowScheduleAsync(CancellationToken.None);
    }

    [RelayCommand]
    private async Task RemoveScheduleAsync()
    {
        if (await dialogs.ConfirmAsync("Remove the daily schedule?", "Updates will run only when you start them.", "Remove",
                ConfirmTone.Destructive))
        {
            await schedule.RemoveAsync();
            await ShowScheduleAsync(CancellationToken.None);
        }
    }

    [RelayCommand]
    private void CreateStartMenuShortcut()
    {
        windows.CreateStartMenuShortcut();
        UpdateText = "EQRisk is in the Start menu.";
    }

    [RelayCommand]
    private async Task CheckUpdatesAsync()
    {
        UpdateText = "Checking…";
        var check = await updates.CheckAsync();
        UpdateText = check.Message;
        if (check.Available && await dialogs.ConfirmAsync($"Update to {check.Version}?",
                "EQRisk downloads the update, closes and starts again. A running job is stopped first.", "Update"))
        {
            await updates.DownloadAndRestartAsync();
        }
    }

    [RelayCommand]
    private async Task SetUpdateTokenAsync()
    {
        var token = await dialogs.AskSecretAsync("GitHub token for updates",
            "A fine-grained token with read access to this repository's releases, needed while the repository is private. " +
            "It is encrypted for your Windows account and used only to check for updates; the engine never receives it.");
        if (!string.IsNullOrWhiteSpace(token))
        {
            vault.Store(KeyCatalog.UpdateToken.Env, token);
            HasUpdateToken = true;
        }
    }

    [RelayCommand]
    private void RemoveUpdateToken()
    {
        vault.Remove(KeyCatalog.UpdateToken.Env);
        HasUpdateToken = false;
    }

    [RelayCommand]
    private void OpenFolder(string path)
    {
        if (!string.IsNullOrWhiteSpace(path) && Directory.Exists(path))
        {
            shell.OpenFolder(path);
        }
    }

    [RelayCommand]
    private void OpenDocument(string name)
    {
        var path = name == "README.md" ? Path.Combine(EngineRoot, name) : Path.Combine(EngineRoot, "docs", name);
        if (File.Exists(path))
        {
            shell.OpenFile(path);
        }
    }

    private void Persist(Func<AppSettings, AppSettings> change)
    {
        if (!loadingSettings)
        {
            _ = settings.SaveAsync(change(settings.Current));
        }
    }

    private void ShowCheck(EngineCheck check)
    {
        EngineOk = check.Ok;
        EngineCheckText = check.Ok
            ? $"eqrisk {check.Engine!.Version}, Python at {check.Engine.PythonExe}"
            : check.Problem ?? "Not a usable engine folder.";
    }

    private async Task ShowScheduleAsync(CancellationToken ct)
    {
        try
        {
            var task = await schedule.GetAsync(ct);
            ScheduleRegistered = task is not null;
            ScheduleText = task is null
                ? "Not registered. Updates run only when you start them."
                : $"{task.State} · next run {Fmt.Time(task.NextRun)} · last run {Fmt.Time(task.LastRun)}" +
                  (task.LastRun is not null ? task.LastRunSucceeded ? " · last result OK" : $" · last result {task.LastResult}" : "") +
                  (task.Command is { } cmd ? $" · {cmd}" : "");
        }
        catch (Exception ex) when (ex is System.Runtime.InteropServices.COMException or PlatformNotSupportedException
                                       or UnauthorizedAccessException)
        {
            ScheduleText = "Task Scheduler could not be read.";
        }
    }
}
