using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Activity;

/// <summary>
/// Every button that starts a job goes through here: it checks the keys a vendor job needs, starts
/// the job, and turns the reasons a job cannot start into a plain message instead of an exception.
/// </summary>
public sealed class JobLauncher(IJobRunner runner, IEngineFeed feed, IKeyVault vault, IDialogs dialogs)
{
    public bool IsBusy => runner.Current is not null;

    /// <summary>Start <paramref name="spec"/>. False, after telling the user why, when it cannot start.</summary>
    public async Task<bool> RunAsync(JobSpec spec, JobTrigger trigger = JobTrigger.App)
    {
        ArgumentNullException.ThrowIfNull(spec);
        if (spec.NeedsKeys && await MissingRequiredKeysAsync() is { Count: > 0 } missing)
        {
            await dialogs.ShowErrorAsync("Keys are needed",
                $"{spec.Label} downloads data and needs {string.Join(", ", missing)}. Add them on the Data page, "
                + "or in the engine's .env file.");
            return false;
        }

        try
        {
            await runner.StartAsync(spec, trigger);
            return true;
        }
        catch (JobBusyException ex)
        {
            await dialogs.ShowErrorAsync("A job is already running", ex.Message);
        }
        catch (InvalidOperationException ex)
        {
            await dialogs.ShowErrorAsync("The engine is not set up", ex.Message);
        }

        return false;
    }

    /// <summary>Required keys set neither in the vault nor in .env; null when .env cannot be checked
    /// (the engine is not running), in which case the engine reports a missing key itself.</summary>
    public async Task<IReadOnlyList<string>?> MissingRequiredKeysAsync()
    {
        IReadOnlyList<KeyPresence> dotEnv;
        try
        {
            dotEnv = await feed.KeysAsync();
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            return null;
        }

        var inDotEnv = dotEnv.Where(k => k.Present).Select(k => k.Env).ToHashSet(StringComparer.Ordinal);
        return KeyCatalog.MissingRequired(KeyCatalog.Resolve(vault.Names, inDotEnv));
    }
}
