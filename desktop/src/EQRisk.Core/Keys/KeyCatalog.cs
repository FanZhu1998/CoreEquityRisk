namespace EQRisk.Core.Keys;

/// <summary>A key the engine reads from its environment or from .env.</summary>
/// <param name="Env">The environment variable name, which is also its name in .env.</param>
/// <param name="Purpose">What the engine uses it for, shown on the Data page.</param>
/// <param name="Required">Whether a daily update needs it.</param>
public sealed record KeyDefinition(string Env, string Purpose, bool Required);

/// <summary>Where a key currently comes from. The vault wins: its value reaches the engine as an
/// environment variable, which pydantic-settings reads before .env.</summary>
public enum KeySource
{
    Missing,
    DotEnv,
    Vault,
}

/// <summary>A key and where it comes from. Carries no value, by design.</summary>
public sealed record KeyStatus(KeyDefinition Definition, KeySource Source)
{
    public bool Configured => Source != KeySource.Missing;
}

public static class KeyCatalog
{
    /// <summary>Keys the engine reads (eqrisk/config.py, class Settings).</summary>
    public static IReadOnlyList<KeyDefinition> Engine { get; } =
    [
        new("EODHD_API_KEY", "Daily prices and volume (EODHD)", Required: true),
        new("FRED_API_KEY", "Risk-free rate (FRED series DTB3)", Required: true),
        new("SEC_USER_AGENT", "SEC EDGAR filings: your name and contact address", Required: true),
        new("DATABENTO_API_KEY", "Databento prices (optional, metered)", Required: false),
        new("NASDAQ_DATA_LINK_API_KEY", "Sharadar via Nasdaq Data Link (optional)", Required: false),
    ];

    /// <summary>A token the app itself uses to read updates from a private GitHub repository. It is
    /// never passed to the engine.</summary>
    public static KeyDefinition UpdateToken { get; } =
        new("GITHUB_UPDATE_TOKEN", "Read-only GitHub token for updates from a private repository", Required: false);

    public static bool IsEngineKey(string env) => Engine.Any(k => k.Env == env);

    /// <summary>Combine what the vault holds with what .env sets. Vault first, as in the engine.</summary>
    public static IReadOnlyList<KeyStatus> Resolve(IReadOnlySet<string> inVault, IReadOnlySet<string> inDotEnv)
    {
        ArgumentNullException.ThrowIfNull(inVault);
        ArgumentNullException.ThrowIfNull(inDotEnv);
        return Engine
            .Select(k => new KeyStatus(k, inVault.Contains(k.Env) ? KeySource.Vault
                : inDotEnv.Contains(k.Env) ? KeySource.DotEnv : KeySource.Missing))
            .ToList();
    }

    public static IReadOnlyList<string> MissingRequired(IEnumerable<KeyStatus> statuses) =>
        statuses.Where(s => s.Definition.Required && !s.Configured).Select(s => s.Definition.Env).ToList();
}
