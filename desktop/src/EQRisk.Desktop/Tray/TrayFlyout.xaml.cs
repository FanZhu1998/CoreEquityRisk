using System.Windows;
using System.Windows.Media;
using EQRisk.Desktop.Native;
using EQRisk.Presentation.Activity;

namespace EQRisk.Desktop.Tray;

/// <summary>The small window the tray icon opens, placed against the taskbar like Windows' own flyouts.</summary>
public partial class TrayFlyout : Window
{
    private const int TaskbarGap = 12;

    public TrayFlyout()
    {
        InitializeComponent();
        SourceInitialized += (_, _) => WindowEffects.ApplyFrame(this, smallCorners: false);
        Deactivated += (_, _) => Hide();
    }

    public event EventHandler? RunRequested;

    public event EventHandler? OpenRequested;

    public void Show(StatusView view, bool canRun)
    {
        ArgumentNullException.ThrowIfNull(view);
        Headline.Text = view.Headline;
        Detail.Text = view.Detail;
        var color = TrayIconRenderer.ToneOf(view.Health);
        Dot.Fill = new SolidColorBrush(Color.FromRgb(color.Red, color.Green, color.Blue));
        RunButton.Visibility = canRun ? Visibility.Visible : Visibility.Collapsed;
    }

    /// <summary>Show next to the notification area and take focus, so a click elsewhere closes it.</summary>
    public void Present()
    {
        Opacity = 0;
        Show();
        UpdateLayout();
        Taskbar.PlaceNearTray(this, TaskbarGap);
        Opacity = 1;
        Activate();
    }

    private void OnRun(object sender, RoutedEventArgs e) => RunRequested?.Invoke(this, EventArgs.Empty);

    private void OnOpen(object sender, RoutedEventArgs e) => OpenRequested?.Invoke(this, EventArgs.Empty);
}
