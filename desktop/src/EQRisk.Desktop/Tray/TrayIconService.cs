using System.ComponentModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Desktop.Views;
using EQRisk.Presentation;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Services;
using H.NotifyIcon;

namespace EQRisk.Desktop.Tray;

/// <summary>
/// What the notification-area icon shows and does: its colour says where the model stands, a ring
/// shows a running update's progress, a click opens the flyout, a double click the window, and a
/// right click the menu. Notifications go through <see cref="TrayIcon"/>, which the job activity
/// this class watches also uses.
/// </summary>
internal sealed class TrayIconService : IDisposable
{
    private static readonly TimeSpan RefreshEvery = TimeSpan.FromMinutes(10);

    private readonly TrayIcon tray;
    private readonly TaskbarIcon icon;
    private readonly IEngineFeed feed;
    private readonly JobLauncher launcher;
    private readonly JobActivityViewModel activity;
    private readonly IAppInfo app;
    private readonly IShell shell;
    private readonly DispatcherTimer refresh;
    private StatusView status = StatusSummary.Unavailable("Starting the engine.");
    private System.Drawing.Icon? drawn;
    private TrayFlyout? flyout;
    private bool disposed;

    public TrayIconService(TrayIcon tray, IEngineFeed feed, JobLauncher launcher, JobActivityViewModel activity, IAppInfo app,
        IShell shell, IMessenger messenger)
    {
        this.tray = tray;
        this.feed = feed;
        this.launcher = launcher;
        this.activity = activity;
        this.app = app;
        this.shell = shell;
        icon = tray.Taskbar;
        icon.ContextMenu = BuildMenu();
        icon.TrayLeftMouseUp += (_, _) => ToggleFlyout();
        icon.TrayMouseDoubleClick += (_, _) => OpenWindow();
        activity.PropertyChanged += OnActivity;
        messenger.Register<TrayIconService, JobFinishedMessage>(this, (recipient, message) => _ = recipient.RefreshAsync());
        refresh = new DispatcherTimer { Interval = RefreshEvery };
        refresh.Tick += (_, _) => _ = RefreshAsync();
    }

    public void Show()
    {
        Redraw();
        icon.ForceCreate(false);                      // efficiency mode is the window's business (NativeMethods)
        refresh.Start();
        _ = RefreshAsync();
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        disposed = true;
        refresh.Stop();
        activity.PropertyChanged -= OnActivity;
        flyout?.Close();
        tray.Dispose();
        drawn?.Dispose();
    }

    private async Task RefreshAsync()
    {
        try
        {
            status = StatusSummary.From(await feed.StatusAsync());
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            status = StatusSummary.Unavailable(Problems.Describe(ex));
        }

        Redraw();
    }

    private void OnActivity(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName is nameof(JobActivityViewModel.Step) or nameof(JobActivityViewModel.IsRunning)
            or nameof(JobActivityViewModel.Job))
        {
            Redraw();
        }
    }

    private StatusView Current()
    {
        if (!activity.IsRunning || activity.Job is not { } job)
        {
            return status;
        }

        var step = activity.ShowsSteps ? activity.StepNames[Math.Clamp(activity.Step, 0, activity.StepNames.Count - 1)] : "";
        return StatusSummary.Running(job.Label, step);
    }

    private void Redraw()
    {
        if (disposed)
        {
            return;
        }

        var view = Current();
        double? progress = activity.IsRunning
            ? activity.ShowsSteps ? (activity.Step + 0.5) / activity.StepNames.Count : 0.25
            : null;
        var next = TrayIconRenderer.Icon(view.Health, progress);
        icon.Icon = next;
        drawn?.Dispose();
        drawn = next;
        var tip = $"EQRisk: {view.Headline}";
        icon.ToolTipText = tip.Length > 120 ? tip[..120] : tip;
        flyout?.Show(view, CanRunUpdate);
    }

    private bool CanRunUpdate => status.Health is Health.Pending && !launcher.IsBusy;

    private void ToggleFlyout()
    {
        if (flyout is { IsVisible: true })
        {
            flyout.Hide();
            return;
        }

        flyout ??= NewFlyout();
        flyout.Show(Current(), CanRunUpdate);
        flyout.Present();
    }

    private TrayFlyout NewFlyout()
    {
        var f = new TrayFlyout();
        f.RunRequested += async (_, _) =>
        {
            f.Hide();
            await launcher.RunAsync(EngineJobs.DailyUpdate(), JobTrigger.Tray);
        };
        f.OpenRequested += (_, _) =>
        {
            f.Hide();
            OpenWindow();
        };
        return f;
    }

    private static void OpenWindow()
    {
        if (Application.Current?.MainWindow is MainWindow window)
        {
            window.Reveal();
        }
    }

    private ContextMenu BuildMenu()
    {
        var menu = new ContextMenu();
        menu.Items.Add(Item("Open EQRisk", OpenWindow, bold: true));
        menu.Items.Add(Item("Run today's update", () => _ = launcher.RunAsync(EngineJobs.DailyUpdate(), JobTrigger.Tray)));
        menu.Items.Add(new Separator());
        menu.Items.Add(Item("Open the app's logs", () => shell.OpenFolder(app.LogsDirectory)));
        menu.Items.Add(new Separator());
        menu.Items.Add(Item("Exit EQRisk", () => _ = App.Current.ExitAsync()));
        return menu;
    }

    private static MenuItem Item(string header, Action act, bool bold = false)
    {
        var item = new MenuItem { Header = header, FontWeight = bold ? FontWeights.SemiBold : FontWeights.Normal };
        item.Click += (_, _) => act();
        return item;
    }
}
