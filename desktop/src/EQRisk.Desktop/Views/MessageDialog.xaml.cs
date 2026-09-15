using System.Windows;
using System.Windows.Input;
using EQRisk.Desktop.Native;

namespace EQRisk.Desktop.Views;

/// <summary>A question or a message, in the app's frame. A destructive action gets a warning button.</summary>
public partial class MessageDialog : Window
{
    public MessageDialog(string title, string message, string confirm, string? cancel, bool destructive)
    {
        InitializeComponent();
        Title = title;
        Heading.Text = title;
        Message.Text = message;
        ConfirmButton.Content = confirm;
        if (destructive)
        {
            ConfirmButton.Style = (Style)FindResource("DangerButton");
        }

        if (cancel is null)
        {
            CancelButton.Visibility = Visibility.Collapsed;
            ConfirmButton.IsCancel = true;
        }
        else
        {
            CancelButton.Content = cancel;
        }

        SourceInitialized += (_, _) => WindowEffects.ApplyFrame(this, smallCorners: true);
        if (Owner is null)
        {
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
        }
    }

    private void OnConfirm(object sender, RoutedEventArgs e) => DialogResult = true;

    private void OnCancel(object sender, RoutedEventArgs e) => DialogResult = false;

    private void OnDrag(object sender, MouseButtonEventArgs e) => DragMove();
}
