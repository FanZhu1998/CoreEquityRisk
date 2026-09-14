namespace EQRisk.Infrastructure;

/// <summary>
/// Where the app keeps its own files: %LOCALAPPDATA%\EQRisk. Settings, the key vault, the job
/// history and job logs live here, outside the repository, so none of them can ever be committed.
/// </summary>
public sealed class AppPaths
{
    public AppPaths()
        : this(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "EQRisk"))
    {
    }

    public AppPaths(string root)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(root);
        Root = root;
    }

    public string Root { get; }

    public string SettingsFile => Path.Combine(Root, "settings.json");

    public string VaultFile => Path.Combine(Root, "vault.json");

    public string Database => Path.Combine(Root, "eqrisk.db");

    public string Logs => Path.Combine(Root, "logs");

    public string JobLogs => Path.Combine(Logs, "jobs");

    public AppPaths EnsureCreated()
    {
        Directory.CreateDirectory(Root);
        Directory.CreateDirectory(JobLogs);
        return this;
    }
}
