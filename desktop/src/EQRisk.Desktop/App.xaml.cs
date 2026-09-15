using System.Windows;
using System.Windows.Threading;
using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;
using EQRisk.Desktop.Hosting;
using EQRisk.Desktop.Native;
using EQRisk.Desktop.Tray;
using EQRisk.Desktop.Views;
using EQRisk.Infrastructure.Engine;
using EQRisk.Presentation;
using EQRisk.Presentation.Services;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace EQRisk.Desktop;

/// <summary>
/// The app: builds the host, shows the window (or only the tray icon), starts the engine, and owns
/// the one way out, which asks before stopping a running job.
/// </summary>
public partial class App : Application
{
    private readonly SingleInstance instance;
    private readonly bool startMinimized;
    private readonly string startPage;
    private IHost? host;
    private bool exiting;

    internal App(SingleInstance instance, bool startMinimized, string? startPage)
    {
        this.instance = instance;
        this.startMinimized = startMinimized;
        this.startPage = startPage ?? "today";
    }

    public IServiceProvider Services => host?.Services ?? throw new InvalidOperationException("The app has not started.");

    public static new App Current => (App)Application.Current;

    /// <summary>True once the user has chosen to leave: windows then close instead of hiding.</summary>
    public bool IsExiting => exiting;

    public void Notify(string title, string message) =>
        Services.GetRequiredService<INotifier>().Notify(title, message, NoticeKind.Info);

    /// <summary>Leave the app. With a job running, ask first; a yes stops the job and everything it started.</summary>
    public async Task ExitAsync()
    {
        if (exiting)
        {
            return;
        }

        var runner = Services.GetRequiredService<IJobRunner>();
        if (runner.Current is { } job)
        {
            var dialogs = Services.GetRequiredService<IDialogs>();
            if (!await dialogs.ConfirmAsync("Stop the running job and exit?",
                    $"{job.Label} is still running. Exiting stops it; you can run it again later.", "Stop and exit",
                    ConfirmTone.Destructive))
            {
                return;
            }

            await runner.StopAsync();
        }

        exiting = true;
        Services.GetRequiredService<TrayIconService>().Dispose();
        await Services.GetRequiredService<EngineConnection>().DisposeAsync();
        await host!.StopAsync(TimeSpan.FromSeconds(5));
        Shutdown();
    }

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        DispatcherUnhandledException += OnUnhandled;
        try
        {
            host = Composition.BuildApp(this);
            await host.StartAsync();
            await Composition.ReconcileAsync(host.Services);

            var window = Services.GetRequiredService<MainWindow>();
            MainWindow = window;
            var tray = Services.GetRequiredService<TrayIconService>();
            tray.Show();
            instance.Listen(() => Dispatcher.BeginInvoke(window.Reveal));
            if (startMinimized)
            {
                window.HideToTray();
            }
            else
            {
                window.Reveal();
            }

            _ = StartEngineAsync();
            await Services.GetRequiredService<ShellViewModel>().NavigateAsync(startPage);
            _ = Composition.CheckForUpdatesAsync(Services);
        }
        catch (Exception ex) when (ex is InvalidOperationException or IOException or UnauthorizedAccessException)
        {
            MessageBox.Show($"EQRisk could not start: {ex.Message}", "EQRisk", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
        }
    }

    protected override void OnExit(ExitEventArgs e)
    {
        host?.Dispose();
        base.OnExit(e);
    }

    private async Task StartEngineAsync()
    {
        try
        {
            await Services.GetRequiredService<EngineConnection>().StartAsync(CancellationToken.None);
        }
        catch (Exception ex) when (ex is EngineException or EngineUnavailableException or TimeoutException)
        {
            // The shell shows the engine's state; pages show why when they load.
        }
    }

    private void OnUnhandled(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        LogUnhandled(Services.GetRequiredService<ILogger<App>>(), e.Exception);
        MessageBox.Show($"Something went wrong: {e.Exception.Message}\n\nThe details are in the app log.", "EQRisk",
            MessageBoxButton.OK, MessageBoxImage.Warning);
        e.Handled = true;
    }

    [LoggerMessage(Level = LogLevel.Error, Message = "Unhandled error in the window")]
    private static partial void LogUnhandled(ILogger logger, Exception error);
}
