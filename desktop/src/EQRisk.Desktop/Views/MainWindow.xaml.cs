using System.ComponentModel;
using System.Windows;
using System.Windows.Data;
using System.Windows.Media;
using EQRisk.Core.Settings;
using EQRisk.Desktop.Native;
using EQRisk.Presentation;

namespace EQRisk.Desktop.Views;

/// <summary>
/// The main window. Closing it keeps EQRisk in the notification area (unless that is switched off),
/// so a scheduled or running update is never cut short by the window going away.
/// </summary>
public partial class MainWindow : Window
{
    private readonly ShellViewModel shell;
    private readonly ISettingsStore settings;
    private bool toldAboutTray;

    public MainWindow(ShellViewModel shell, ISettingsStore settings)
    {
        this.shell = shell;
        this.settings = settings;
        InitializeComponent();
        DataContext = shell;
        CollectionViewSource.GetDefaultView(shell.Entries).GroupDescriptions.Add(new PropertyGroupDescription(nameof(NavEntry.Group)));
        SourceInitialized += (_, _) =>
        {
            WindowEffects.ApplyFrame(this);
            RestorePlacement();
        };
        shell.PropertyChanged += OnShellChanged;
        UpdateEngineDot();
    }

    /// <summary>Bring the window forward from the tray, the taskbar or a second start.</summary>
    public void Reveal()
    {
        EfficiencyMode.Set(false);
        Show();
        if (WindowState == WindowState.Minimized)
        {
            WindowState = WindowState.Normal;
        }

        Activate();
        Topmost = true;
        Topmost = false;
        Focus();
    }

    public void HideToTray()
    {
        Hide();
        if (settings.Current.EfficiencyModeWhenHidden)
        {
            EfficiencyMode.Set(true);
        }
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        ArgumentNullException.ThrowIfNull(e);
        SavePlacement();
        if (App.Current.IsExiting)
        {
            base.OnClosing(e);
            return;
        }

        e.Cancel = true;
        if (settings.Current.CloseToTray)
        {
            HideToTray();
            if (!toldAboutTray)
            {
                toldAboutTray = true;
                App.Current.Notify("EQRisk is still running",
                    "It stays in the notification area to run updates. Right-click its icon to exit.");
            }
        }
        else
        {
            _ = App.Current.ExitAsync();
        }
    }

    private void OnShellChanged(object? sender, PropertyChangedEventArgs e)
    {
        if (e.PropertyName == nameof(ShellViewModel.Current))
        {
            PageScroller.ScrollToTop();
        }
        else if (e.PropertyName is nameof(ShellViewModel.EngineReady) or nameof(ShellViewModel.EngineProblem))
        {
            UpdateEngineDot();
        }
    }

    private void UpdateEngineDot() =>
        EngineDot.Fill = (Brush)FindResource(shell.EngineReady ? "BullBrush" : shell.EngineProblem ? "BearBrush" : "WarnBrush");

    private void RestorePlacement()
    {
        if (settings.Current.Window is not { } w)
        {
            return;
        }

        var screen = new Rect(SystemParameters.VirtualScreenLeft, SystemParameters.VirtualScreenTop,
            SystemParameters.VirtualScreenWidth, SystemParameters.VirtualScreenHeight);
        var placed = new Rect(w.Left, w.Top, w.Width, w.Height);
        if (!screen.IntersectsWith(placed) || w.Width < MinWidth || w.Height < MinHeight)
        {
            return;                                           // a monitor that is gone: keep the default
        }

        WindowStartupLocation = WindowStartupLocation.Manual;
        Left = w.Left;
        Top = w.Top;
        Width = w.Width;
        Height = w.Height;
        if (w.Maximized)
        {
            WindowState = WindowState.Maximized;
        }
    }

    private void SavePlacement()
    {
        var r = WindowState == WindowState.Normal ? new Rect(Left, Top, Width, Height) : RestoreBounds;
        if (r.IsEmpty)
        {
            return;
        }

        var placement = new WindowPlacement(r.Left, r.Top, r.Width, r.Height, WindowState == WindowState.Maximized);
        _ = settings.SaveAsync(settings.Current with { Window = placement });
    }
}
