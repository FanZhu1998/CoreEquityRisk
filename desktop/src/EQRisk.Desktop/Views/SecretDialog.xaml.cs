using System.Windows;
using System.Windows.Input;
using EQRisk.Desktop.Native;

namespace EQRisk.Desktop.Views;

/// <summary>
/// Asks for one secret in a password box. There is no way to reveal it: the value leaves once,
/// through <see cref="TakeValue"/>, which also clears the box.
/// </summary>
public partial class SecretDialog : Window
{
    public SecretDialog(string title, string message)
    {
        InitializeComponent();
        Title = title;
        Heading.Text = title;
        Message.Text = message;
        SourceInitialized += (_, _) => WindowEffects.ApplyFrame(this, smallCorners: true);
        Loaded += (_, _) => Secret.Focus();
        if (Owner is null)
        {
            WindowStartupLocation = WindowStartupLocation.CenterScreen;
        }
    }

    public string TakeValue()
    {
        var value = Secret.Password;
        Secret.Clear();
        return value;
    }

    private void OnChanged(object sender, RoutedEventArgs e) => SaveButton.IsEnabled = Secret.Password.Trim().Length > 0;

    private void OnSave(object sender, RoutedEventArgs e) => DialogResult = true;

    private void OnDrag(object sender, MouseButtonEventArgs e) => DragMove();
}
