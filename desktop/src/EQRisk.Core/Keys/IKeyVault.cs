namespace EQRisk.Core.Keys;

/// <summary>
/// Keys kept by the app, encrypted for the current Windows user (DPAPI). A value goes in once and
/// comes out only to be placed in a job's environment; nothing lists, logs or displays it.
/// </summary>
public interface IKeyVault
{
    /// <summary>Names of the keys the vault holds.</summary>
    IReadOnlySet<string> Names { get; }

    void Store(string name, string value);

    bool Remove(string name);

    /// <summary>Decrypted values for the given names that the vault holds, for one process environment.</summary>
    IReadOnlyDictionary<string, string> ReadForProcess(IEnumerable<string> names);
}
