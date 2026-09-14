using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using EQRisk.Core.Keys;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Storage;

/// <summary>
/// API keys encrypted with Windows DPAPI for the current user (%LOCALAPPDATA%\EQRisk\vault.json).
/// Only that Windows account on this machine can decrypt them; a copy of the file is useless
/// anywhere else. The file stores names in the clear and values only as ciphertext. Values are
/// decrypted solely to be placed in one job's environment, and plaintext buffers are wiped.
/// </summary>
public sealed partial class DpapiKeyVault : IKeyVault
{
    // Entropy separates this app's blobs from any other DPAPI user of the same account. It need not
    // be secret; DPAPI's protection comes from the user's credentials.
    private static readonly byte[] Entropy = "EQRisk key vault, version 1"u8.ToArray();
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    private static readonly HashSet<string> Allowed =
        [.. KeyCatalog.Engine.Select(k => k.Env), KeyCatalog.UpdateToken.Env];

    private readonly AppPaths paths;
    private readonly ILogger<DpapiKeyVault> log;
    private readonly Lock gate = new();
    private readonly Dictionary<string, string> entries;

    public DpapiKeyVault(AppPaths paths, ILogger<DpapiKeyVault> log)
    {
        ArgumentNullException.ThrowIfNull(paths);
        this.paths = paths;
        this.log = log;
        entries = Load();
    }

    public IReadOnlySet<string> Names
    {
        get
        {
            lock (gate)
            {
                return entries.Keys.ToHashSet(StringComparer.Ordinal);
            }
        }
    }

    public void Store(string name, string value)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(name);
        ArgumentException.ThrowIfNullOrWhiteSpace(value);
        if (!Allowed.Contains(name))
        {
            throw new ArgumentException($"{name} is not a key this app keeps.", nameof(name));
        }

        var plain = Encoding.UTF8.GetBytes(value.Trim());
        try
        {
            var cipher = ProtectedData.Protect(plain, Entropy, DataProtectionScope.CurrentUser);
            lock (gate)
            {
                entries[name] = Convert.ToBase64String(cipher);
                Save();
            }
        }
        finally
        {
            CryptographicOperations.ZeroMemory(plain);
        }

        LogStored(name);
    }

    public bool Remove(string name)
    {
        lock (gate)
        {
            if (!entries.Remove(name))
            {
                return false;
            }

            Save();
        }

        LogRemoved(name);
        return true;
    }

    public IReadOnlyDictionary<string, string> ReadForProcess(IEnumerable<string> names)
    {
        ArgumentNullException.ThrowIfNull(names);
        var result = new Dictionary<string, string>(StringComparer.Ordinal);
        lock (gate)
        {
            foreach (var name in names)
            {
                if (!entries.TryGetValue(name, out var stored))
                {
                    continue;
                }

                byte[]? plain = null;
                try
                {
                    plain = ProtectedData.Unprotect(Convert.FromBase64String(stored), Entropy,
                        DataProtectionScope.CurrentUser);
                    result[name] = Encoding.UTF8.GetString(plain);
                }
                catch (Exception ex) when (ex is CryptographicException or FormatException)
                {
                    LogUnreadable(name);          // made by another Windows account, or damaged
                }
                finally
                {
                    if (plain is not null)
                    {
                        CryptographicOperations.ZeroMemory(plain);
                    }
                }
            }
        }

        return result;
    }

    private Dictionary<string, string> Load()
    {
        if (!File.Exists(paths.VaultFile))
        {
            return new(StringComparer.Ordinal);
        }

        try
        {
            var file = JsonSerializer.Deserialize<VaultFile>(File.ReadAllText(paths.VaultFile), Json);
            return new(file?.Entries ?? [], StringComparer.Ordinal);
        }
        catch (JsonException)
        {
            LogVaultUnreadable();
            File.Copy(paths.VaultFile, paths.VaultFile + ".bad", overwrite: true);
            return new(StringComparer.Ordinal);
        }
    }

    private void Save()
    {
        paths.EnsureCreated();
        var temp = paths.VaultFile + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(new VaultFile(1, entries), Json));
        File.Move(temp, paths.VaultFile, overwrite: true);
    }

    private sealed record VaultFile(int Schema, Dictionary<string, string> Entries);

    [LoggerMessage(Level = LogLevel.Information, Message = "Key stored in the vault: {Name}")]
    private partial void LogStored(string name);

    [LoggerMessage(Level = LogLevel.Information, Message = "Key removed from the vault: {Name}")]
    private partial void LogRemoved(string name);

    [LoggerMessage(Level = LogLevel.Warning,
        Message = "The vault holds {Name} but this Windows account cannot decrypt it; set it again")]
    private partial void LogUnreadable(string name);

    [LoggerMessage(Level = LogLevel.Warning, Message = "vault.json could not be read; kept as vault.json.bad")]
    private partial void LogVaultUnreadable();
}
