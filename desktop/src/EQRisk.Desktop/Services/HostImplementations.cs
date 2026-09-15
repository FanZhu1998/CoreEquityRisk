using System.Diagnostics;
using System.Net.Http;
using System.Reflection;
using System.Windows;
using System.Windows.Threading;
using EQRisk.Core.Keys;
using EQRisk.Desktop.Native;
using EQRisk.Desktop.Views;
using EQRisk.Infrastructure;
using EQRisk.Presentation.Services;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;
using Velopack;
using Velopack.Sources;

namespace EQRisk.Desktop.Services;

internal sealed class WpfDispatcher(Dispatcher dispatcher) : IUiDispatcher
{
    public void Post(Action action)
    {
        if (dispatcher.CheckAccess())
        {
            action();
        }
        else
        {
            dispatcher.BeginInvoke(action);
        }
    }
}

internal sealed class WindowsShell : IShell
{
    public void OpenFolder(string path)
    {
        if (Directory.Exists(path))
        {
            Launch("explorer.exe", path);
        }
    }

    public void OpenFile(string path)
    {
        if (File.Exists(path))
        {
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true })?.Dispose();
        }
    }

    public void OpenUrl(Uri url)
    {
        ArgumentNullException.ThrowIfNull(url);
        if (url.Scheme is "http" or "https")
        {
            Process.Start(new ProcessStartInfo(url.AbsoluteUri) { UseShellExecute = true })?.Dispose();
        }
    }

    private static void Launch(string exe, string argument)
    {
        var psi = new ProcessStartInfo(exe) { UseShellExecute = false };
        psi.ArgumentList.Add(argument);
        Process.Start(psi)?.Dispose();
    }
}

internal sealed class AppInfo(AppPaths paths) : IAppInfo
{
    public string Version { get; } =
        typeof(AppInfo).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion
            .Split('+')[0] ?? "0.0.0";

    public string AppDirectory => AppContext.BaseDirectory;

    // Velopack keeps the app in <install>\current\ and updates it in place, so this path is stable.
    public string LauncherPath => Environment.ProcessPath ?? Path.Combine(AppContext.BaseDirectory, "EQRisk.exe");

    public string DataDirectory => paths.Root;

    public string LogsDirectory => paths.Logs;

    public bool IsInstalled
    {
        get
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar));
            return dir.Name.Equals("current", StringComparison.OrdinalIgnoreCase) && dir.Parent is { } root &&
                   File.Exists(Path.Combine(root.FullName, "Update.exe"));
        }
    }
}

/// <summary>Updates from GitHub Releases through Velopack, for copies installed with Setup.exe.</summary>
internal sealed partial class VelopackUpdates(IKeyVault vault, IAppInfo app, ILogger<VelopackUpdates> log) : IUpdateService
{
    public const string Repository = "https://github.com/FanZhu1998/CoreEquityRisk";

    private UpdateInfo? pending;
    private UpdateManager? manager;

    public bool CanUpdate => app.IsInstalled;

    public async Task<UpdateCheck> CheckAsync(CancellationToken ct = default)
    {
        if (!CanUpdate)
        {
            return new UpdateCheck(false, null,
                "Updates apply to copies installed with Setup.exe. This one is portable or a development build.");
        }

        try
        {
            // The token, if set, is read only here and only for GitHub; the engine never sees it.
            var token = vault.ReadForProcess([KeyCatalog.UpdateToken.Env]).GetValueOrDefault(KeyCatalog.UpdateToken.Env);
            manager = new UpdateManager(new GithubSource(Repository, token, prerelease: false));
            pending = await manager.CheckForUpdatesAsync();
            return pending is null
                ? new UpdateCheck(false, null, $"EQRisk {app.Version} is the latest release.")
                : new UpdateCheck(true, pending.TargetFullRelease.Version.ToString(),
                    $"EQRisk {pending.TargetFullRelease.Version} is available.");
        }
        catch (Exception ex) when (ex is HttpRequestException or InvalidOperationException or IOException)
        {
            LogCheckFailed(ex.Message);
            return new UpdateCheck(false, null,
                "GitHub Releases could not be reached. While the repository is private, updates need a read-only token.");
        }
    }

    public async Task DownloadAndRestartAsync(CancellationToken ct = default)
    {
        if (manager is null || pending is null)
        {
            return;
        }

        await manager.DownloadUpdatesAsync(pending, null, ct);
        manager.ApplyUpdatesAndRestart(pending.TargetFullRelease);
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Update check failed: {Reason}")]
    private partial void LogCheckFailed(string reason);
}

/// <summary>Start with Windows (the per-user Run key) and a Start menu shortcut.</summary>
internal sealed class WindowsIntegration(IAppInfo app) : IWindowsIntegration
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "EQRisk";

    public bool StartsWithWindows
    {
        get
        {
            using var key = Registry.CurrentUser.OpenSubKey(RunKey);
            return key?.GetValue(ValueName) is string value && value.Equals(Command, StringComparison.OrdinalIgnoreCase);
        }
    }

    public bool HasStartMenuShortcut => File.Exists(ShortcutPath);

    private string Command => $"\"{app.LauncherPath}\" {Program.MinimizedArgument}";

    private static string ShortcutPath =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "EQRisk.lnk");

    public void SetStartWithWindows(bool enabled)
    {
        using var key = Registry.CurrentUser.CreateSubKey(RunKey);
        if (enabled)
        {
            key.SetValue(ValueName, Command);
        }
        else
        {
            key.DeleteValue(ValueName, throwOnMissingValue: false);
        }
    }

    public void CreateStartMenuShortcut() => ShellLink.Create(ShortcutPath, app.LauncherPath, "", app.AppDirectory,
        "EQRisk: daily equity factor risk model");
}

/// <summary>The app's dialogs, in its own frame and palette.</summary>
internal sealed class WpfDialogs : IDialogs
{
    private static Window? Owner =>
        Application.Current?.Windows.OfType<Window>().FirstOrDefault(w => w.IsActive && w.IsVisible)
        ?? (Application.Current?.MainWindow is { IsVisible: true } main ? main : null);

    public Task<bool> ConfirmAsync(string title, string message, string confirm, ConfirmTone tone = ConfirmTone.Normal)
    {
        var dialog = new MessageDialog(title, message, confirm, "Cancel", tone == ConfirmTone.Destructive) { Owner = Owner };
        return Task.FromResult(dialog.ShowDialog() == true);
    }

    public Task ShowErrorAsync(string title, string message)
    {
        new MessageDialog(title, message, "OK", null, destructive: false) { Owner = Owner }.ShowDialog();
        return Task.CompletedTask;
    }

    public Task<string?> AskSecretAsync(string title, string message)
    {
        var dialog = new SecretDialog(title, message) { Owner = Owner };
        return Task.FromResult(dialog.ShowDialog() == true ? dialog.TakeValue() : null);
    }

    public string? PickFolder(string title, string? initialFolder)
    {
        var dialog = new OpenFolderDialog { Title = title };
        if (Directory.Exists(initialFolder))
        {
            dialog.InitialDirectory = initialFolder;
        }

        return dialog.ShowDialog(Owner) == true ? dialog.FolderName : null;
    }

    public string? PickFile(string title, string filter, string? initialFolder)
    {
        var dialog = new OpenFileDialog { Title = title, Filter = filter };
        if (Directory.Exists(initialFolder))
        {
            dialog.InitialDirectory = initialFolder;
        }

        return dialog.ShowDialog(Owner) == true ? dialog.FileName : null;
    }

    public string? PickSaveFile(string title, string filter, string fileName, string? initialFolder)
    {
        var dialog = new SaveFileDialog { Title = title, Filter = filter, FileName = fileName };
        if (Directory.Exists(initialFolder))
        {
            dialog.InitialDirectory = initialFolder;
        }

        return dialog.ShowDialog(Owner) == true ? dialog.FileName : null;
    }
}
