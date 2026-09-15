using System.Windows;
using EQRisk.Desktop.Hosting;
using EQRisk.Desktop.Native;
using Velopack;

namespace EQRisk.Desktop;

/// <summary>
/// The entry point. Velopack runs first (install, update and uninstall hooks), then the app decides
/// what it is: the scheduled daily update with no window, a second copy that hands over to the
/// first, or the app itself.
/// </summary>
public static class Program
{
    /// <summary>Windows Task Scheduler starts the app with this to run the daily update, headless.</summary>
    public const string RunDailyArgument = "--run-daily";

    /// <summary>Start in the notification area (Start with Windows uses it).</summary>
    public const string MinimizedArgument = "--minimized";

    /// <summary>Open at a page other than Today: <c>--page data</c>.</summary>
    public const string PageArgument = "--page";

    /// <summary>Windows 10 version 2004, the oldest this app supports.</summary>
    private const int MinimumBuild = 19041;

    [STAThread]
    public static int Main(string[] args)
    {
        VelopackApp.Build().Run();

        if (Environment.OSVersion.Version.Build < MinimumBuild)
        {
            MessageBox.Show("EQRisk needs Windows 10 version 2004 or later, or Windows 11.", "EQRisk",
                MessageBoxButton.OK, MessageBoxImage.Error);
            return 3;
        }

        if (args.Contains(RunDailyArgument, StringComparer.OrdinalIgnoreCase))
        {
            return Headless.RunDailyAsync().GetAwaiter().GetResult();
        }

        using var instance = SingleInstance.Acquire();
        if (!instance.IsFirst)
        {
            instance.ActivateFirst();
            return 0;
        }

        var page = args.SkipWhile(a => !a.Equals(PageArgument, StringComparison.OrdinalIgnoreCase)).Skip(1).FirstOrDefault();
        var app = new App(instance, args.Contains(MinimizedArgument, StringComparer.OrdinalIgnoreCase), page);
        app.InitializeComponent();
        return app.Run();
    }
}
