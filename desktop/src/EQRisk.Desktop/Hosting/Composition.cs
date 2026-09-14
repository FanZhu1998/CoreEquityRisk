using System.Globalization;
using System.Windows.Threading;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Engine;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Core.Settings;
using EQRisk.Desktop.Services;
using EQRisk.Desktop.Tray;
using EQRisk.Desktop.Views;
using EQRisk.Infrastructure;
using EQRisk.Infrastructure.Engine;
using EQRisk.Infrastructure.Jobs;
using EQRisk.Infrastructure.Storage;
using EQRisk.Infrastructure.Windows;
using EQRisk.Presentation;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Pages;
using EQRisk.Presentation.Services;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Serilog;
using Serilog.Events;

namespace EQRisk.Desktop.Hosting;

/// <summary>
/// The composition root: every service, view model and window is registered here and nowhere else.
/// The headless scheduled run shares the storage, engine and job services but none of the UI.
/// </summary>
internal static class Composition
{
    private const int LogFilesKept = 14;

    public static IHost BuildApp(App app)
    {
        var builder = NewBuilder(out var paths);
        AddShared(builder.Services, paths);
        AddWindow(builder.Services, app.Dispatcher);
        return builder.Build();
    }

    public static IHost BuildHeadless()
    {
        var builder = NewBuilder(out var paths);
        AddShared(builder.Services, paths);
        builder.Services.AddSingleton<ScheduledUpdate>();
        return builder.Build();
    }

    /// <summary>What only the window needs: the engine's read server, the view models, the tray and
    /// the window itself.</summary>
    internal static void AddWindow(IServiceCollection s, Dispatcher dispatcher)
    {
        s.AddSingleton(sp => new EngineConnection(sp.GetRequiredService<IEngineLocator>(),
            sp.GetRequiredService<ISettingsStore>(), sp.GetRequiredService<ILogger<EngineConnection>>(),
            AppContext.BaseDirectory));
        s.AddSingleton<IEngineConnection>(sp => sp.GetRequiredService<EngineConnection>());
        s.AddSingleton<IEngineFeed, EngineFeed>();
        s.AddSingleton<IViewerServer>(sp => new ViewerServer(sp.GetRequiredService<IEngineLocator>(),
            sp.GetRequiredService<ISettingsStore>(), sp.GetRequiredService<ILogger<ViewerServer>>(), AppContext.BaseDirectory));

        s.AddSingleton<IMessenger>(WeakReferenceMessenger.Default);
        s.AddSingleton<IUiDispatcher>(new WpfDispatcher(dispatcher));
        s.AddSingleton<IDialogs, WpfDialogs>();
        s.AddSingleton<IShell, WindowsShell>();
        s.AddSingleton<IAppInfo, AppInfo>();
        s.AddSingleton<IUpdateService, VelopackUpdates>();
        s.AddSingleton<IWindowsIntegration, WindowsIntegration>();

        // The icon and its notifications are a leaf. The job activity notifies through it, and the
        // tray's status and menu watch the job activity, so one class doing both would need itself.
        s.AddSingleton<TrayIcon>();
        s.AddSingleton<INotifier>(sp => sp.GetRequiredService<TrayIcon>());
        s.AddSingleton<TrayIconService>();

        s.AddSingleton<JobLauncher>();
        s.AddSingleton<JobActivityViewModel>();
        s.AddSingleton<ShellViewModel>();
        s.AddSingleton<PageViewModel, TodayViewModel>();
        s.AddSingleton<PageViewModel, DataViewModel>();
        s.AddSingleton<PageViewModel, EstimateViewModel>();
        s.AddSingleton<PageViewModel, ValidateViewModel>();
        s.AddSingleton<PageViewModel, FactorReturnsViewModel>();
        s.AddSingleton<PageViewModel, FactorRiskViewModel>();
        s.AddSingleton<PageViewModel, ExposuresViewModel>();
        s.AddSingleton<PageViewModel, SpecificRiskViewModel>();
        s.AddSingleton<PageViewModel, PortfolioViewModel>();
        s.AddSingleton<PageViewModel, OptimizerViewModel>();
        s.AddSingleton<PageViewModel, JobsViewModel>();
        s.AddSingleton<PageViewModel, PublishViewModel>();
        s.AddSingleton<PageViewModel, SettingsViewModel>();
        s.AddSingleton<MainWindow>();
    }

    /// <summary>Jobs left marked running by a crash become interrupted, unless one is truly still
    /// running (a scheduled update started while the app was closed).</summary>
    public static async Task ReconcileAsync(IServiceProvider services)
    {
        var probe = services.GetRequiredService<IEngineProcessProbe>();
        if (probe.FindJobs([]).Count == 0)
        {
            await services.GetRequiredService<IJobStore>().MarkInterruptedAsync(TimeProvider.System.GetUtcNow());
        }
    }

    public static async Task CheckForUpdatesAsync(IServiceProvider services)
    {
        var settings = services.GetRequiredService<ISettingsStore>().Current;
        var updates = services.GetRequiredService<IUpdateService>();
        if (!settings.CheckForUpdates || !updates.CanUpdate)
        {
            return;
        }

        var check = await updates.CheckAsync();
        if (check.Available)
        {
            services.GetRequiredService<INotifier>().Notify("An update is available",
                $"EQRisk {check.Version} is ready to install from Settings.", NoticeKind.Info);
        }
    }

    private static HostApplicationBuilder NewBuilder(out AppPaths paths)
    {
        paths = new AppPaths().EnsureCreated();
        var builder = Host.CreateApplicationBuilder(new HostApplicationBuilderSettings
        {
            DisableDefaults = true,
            ContentRootPath = AppContext.BaseDirectory,
        });
        var logs = Path.Combine(paths.Logs, "eqrisk-.log");
        builder.Services.AddSerilog(lc => lc
            .MinimumLevel.Information()
            .MinimumLevel.Override("Microsoft", LogEventLevel.Warning)
            .Enrich.FromLogContext()
            .WriteTo.File(logs, formatProvider: CultureInfo.InvariantCulture, rollingInterval: RollingInterval.Day,
                retainedFileCountLimit: LogFilesKept, shared: true,
                outputTemplate: "{Timestamp:yyyy-MM-dd HH:mm:ss.fff zzz} [{Level:u3}] {SourceContext}: {Message:lj}{NewLine}{Exception}"));
        return builder;
    }

    /// <summary>What the window and the headless scheduled run share: storage, the vault and jobs.</summary>
    internal static void AddShared(IServiceCollection s, AppPaths paths)
    {
        s.AddSingleton(paths);
        s.AddSingleton(TimeProvider.System);
        s.AddSingleton<ISettingsStore, JsonSettingsStore>();
        s.AddSingleton<IEngineLocator, EngineLocator>();
        s.AddSingleton<IKeyVault, DpapiKeyVault>();
        s.AddSingleton<IJobStore, SqliteJobStore>();
        s.AddSingleton<IEngineProcessProbe, WmiEngineProcessProbe>();
        s.AddSingleton<IScheduledTaskService>(sp => new ScheduledTaskService(sp.GetRequiredService<ILogger<ScheduledTaskService>>()));
        s.AddSingleton(sp => new JobRunner(sp.GetRequiredService<IEngineLocator>(), sp.GetRequiredService<ISettingsStore>(),
            sp.GetRequiredService<IJobStore>(), sp.GetRequiredService<IKeyVault>(), sp.GetRequiredService<IEngineProcessProbe>(),
            paths, sp.GetRequiredService<TimeProvider>(), sp.GetRequiredService<ILogger<JobRunner>>(), AppContext.BaseDirectory));
        s.AddSingleton<IJobRunner>(sp => sp.GetRequiredService<JobRunner>());
    }
}

/// <summary>What <c>EQRisk.exe --run-daily</c> runs: the daily update, with no window at all.</summary>
internal static class Headless
{
    public static async Task<int> RunDailyAsync()
    {
        using var host = Composition.BuildHeadless();
        await host.StartAsync();
        try
        {
            return await host.Services.GetRequiredService<ScheduledUpdate>().RunAsync(CancellationToken.None);
        }
        finally
        {
            await host.StopAsync();
            await Log.CloseAndFlushAsync();
        }
    }
}
