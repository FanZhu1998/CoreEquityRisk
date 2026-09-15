using EQRisk.Core.Keys;

namespace EQRisk.Infrastructure.Engine;

/// <summary>
/// The environment every engine process starts with. Python is told to speak UTF-8 and not to
/// buffer, and nothing from a user-level Python setup can redirect which interpreter or packages
/// run. Keys go in only when a caller passes them.
/// </summary>
internal static class EngineEnvironment
{
    private static readonly string[] PythonOverrides = ["PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"];

    /// <param name="env">The process environment to prepare (a copy of the app's).</param>
    /// <param name="keys">Values from the vault to place in the environment, or null for none.</param>
    /// <param name="scrubInheritedKeys">Remove key variables the app itself inherited. True for processes
    /// that must not hold keys: the read server and every job that does not call a vendor.</param>
    public static void Prepare(IDictionary<string, string?> env, IReadOnlyDictionary<string, string>? keys,
        bool scrubInheritedKeys)
    {
        ArgumentNullException.ThrowIfNull(env);
        foreach (var name in PythonOverrides)
        {
            env.Remove(name);
        }

        // The app's own update token never reaches the engine, whatever the process.
        env.Remove(KeyCatalog.UpdateToken.Env);
        if (scrubInheritedKeys)
        {
            foreach (var key in KeyCatalog.Engine)
            {
                env.Remove(key.Env);
            }
        }

        env["PYTHONUTF8"] = "1";
        env["PYTHONIOENCODING"] = "utf-8";
        env["PYTHONUNBUFFERED"] = "1";
        if (keys is null)
        {
            return;
        }

        foreach (var (name, value) in keys)
        {
            if (!KeyCatalog.IsEngineKey(name))
            {
                throw new ArgumentException($"{name} is not an engine key", nameof(keys));
            }

            env[name] = value;
        }
    }
}
