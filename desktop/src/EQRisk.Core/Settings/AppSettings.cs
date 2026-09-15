namespace EQRisk.Core.Settings;

/// <summary>
/// The app's own settings, stored as JSON in %LOCALAPPDATA%\EQRisk\settings.json. Nothing here is
/// secret: keys live in the DPAPI vault or in the engine's .env, never in this file.
/// </summary>
public sealed record AppSettings
{
    public const int CurrentSchema = 1;

    public int Schema { get; init; } = CurrentSchema;

    /// <summary>The engine folder (the CoreEquityRisk checkout: configs/, data/, .venv). Null until chosen
    /// or found next to the app.</summary>
    public string? EngineRoot { get; init; }

    /// <summary>Closing the window keeps EQRisk running in the notification area.</summary>
    public bool CloseToTray { get; init; } = true;

    public bool StartWithWindows { get; init; }

    /// <summary>Ask Windows to run the app in efficiency mode while its window is hidden.</summary>
    public bool EfficiencyModeWhenHidden { get; init; } = true;

    /// <summary>Show a notification when a job finishes and the window is hidden.</summary>
    public bool NotifyOnFinish { get; init; } = true;

    public bool CheckForUpdates { get; init; } = true;

    /// <summary>Time of day, local, for the Windows scheduled task that runs the daily update.</summary>
    public TimeOnly ScheduleTime { get; init; } = new(6, 30);

    public WindowPlacement? Window { get; init; }
}

/// <summary>Where the main window was last, restored at the next start.</summary>
public sealed record WindowPlacement(double Left, double Top, double Width, double Height, bool Maximized);
