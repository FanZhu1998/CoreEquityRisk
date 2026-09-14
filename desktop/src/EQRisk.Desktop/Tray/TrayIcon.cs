using System.Windows;
using EQRisk.Presentation.Services;
using H.NotifyIcon;
using H.NotifyIcon.Core;

namespace EQRisk.Desktop.Tray;

/// <summary>
/// The notification-area icon itself and the notifications that come from it. It depends on nothing,
/// so anything may notify through it; <see cref="TrayIconService"/> decides what the icon shows.
/// </summary>
internal sealed class TrayIcon : INotifier, IDisposable
{
    private bool disposed;

    public TaskbarIcon Taskbar { get; } = new() { ToolTipText = "EQRisk", NoLeftClickDelay = true };

    public void Notify(string title, string message, NoticeKind kind)
    {
        if (disposed || Application.Current?.MainWindow is { IsVisible: true, IsActive: true })
        {
            return;                                   // the window is in front and already says it
        }

        var glyph = kind switch
        {
            NoticeKind.Error => NotificationIcon.Error,
            NoticeKind.Warning => NotificationIcon.Warning,
            _ => NotificationIcon.Info,
        };
        Taskbar.ShowNotification(title, message, glyph, null, false, true, true, false, null);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        disposed = true;
        Taskbar.Dispose();
    }
}
