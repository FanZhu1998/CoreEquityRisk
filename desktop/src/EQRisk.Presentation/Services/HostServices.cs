namespace EQRisk.Presentation.Services;

// What the view models need from the Windows host. The Desktop project implements each one; tests
// use fakes. Keeping them here is what lets every view model run without a window.

/// <summary>Runs work on the UI thread.</summary>
public interface IUiDispatcher
{
    /// <summary>Run <paramref name="action"/> on the UI thread: at once if already there, else queued.</summary>
    void Post(Action action);
}

public enum ConfirmTone
{
    Normal,

    /// <summary>The action replaces or deletes something; the button is drawn as a warning.</summary>
    Destructive,
}

public interface IDialogs
{
    Task<bool> ConfirmAsync(string title, string message, string confirm, ConfirmTone tone = ConfirmTone.Normal);

    Task ShowErrorAsync(string title, string message);

    /// <summary>Ask for a secret in a password box. The value is returned once and never displayed;
    /// null when cancelled.</summary>
    Task<string?> AskSecretAsync(string title, string message);

    string? PickFolder(string title, string? initialFolder);

    string? PickFile(string title, string filter, string? initialFolder);

    string? PickSaveFile(string title, string filter, string fileName, string? initialFolder);
}

public interface IShell
{
    void OpenFolder(string path);

    void OpenFile(string path);

    void OpenUrl(Uri url);
}

public enum NoticeKind
{
    Info,
    Success,
    Warning,
    Error,
}

/// <summary>A notification from the tray icon, for when the window is not in front.</summary>
public interface INotifier
{
    void Notify(string title, string message, NoticeKind kind);
}

public interface IAppInfo
{
    string Version { get; }

    string AppDirectory { get; }

    /// <summary>What the scheduled task should start: the installer's stable launcher when installed,
    /// this executable otherwise.</summary>
    string LauncherPath { get; }

    /// <summary>%LOCALAPPDATA%\EQRisk.</summary>
    string DataDirectory { get; }

    string LogsDirectory { get; }

    /// <summary>Installed with Setup.exe (Velopack), so it can update itself.</summary>
    bool IsInstalled { get; }
}

public sealed record UpdateCheck(bool Available, string? Version, string Message);

public interface IUpdateService
{
    bool CanUpdate { get; }

    Task<UpdateCheck> CheckAsync(CancellationToken ct = default);

    Task DownloadAndRestartAsync(CancellationToken ct = default);
}

/// <summary>Start with Windows and the Start menu entry, for copies not installed by Setup.exe.</summary>
public interface IWindowsIntegration
{
    bool StartsWithWindows { get; }

    void SetStartWithWindows(bool enabled);

    bool HasStartMenuShortcut { get; }

    void CreateStartMenuShortcut();
}
