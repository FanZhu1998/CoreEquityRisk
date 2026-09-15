using System.Text.Json;
using System.Text.Json.Serialization;
using EQRisk.Core.Settings;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Storage;

/// <summary>
/// App settings as JSON in %LOCALAPPDATA%\EQRisk\settings.json, written atomically. An unreadable file
/// is kept aside as settings.json.bad and the app starts from defaults rather than failing.
/// </summary>
public sealed partial class JsonSettingsStore : ISettingsStore, IDisposable
{
    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        Converters = { new JsonStringEnumConverter() },
    };

    private readonly AppPaths paths;
    private readonly ILogger<JsonSettingsStore> log;
    private readonly SemaphoreSlim writeLock = new(1, 1);

    public JsonSettingsStore(AppPaths paths, ILogger<JsonSettingsStore> log)
    {
        ArgumentNullException.ThrowIfNull(paths);
        this.paths = paths;
        this.log = log;
        Current = Load();
    }

    public AppSettings Current { get; private set; }

    public event EventHandler<AppSettings>? Changed;

    public async Task SaveAsync(AppSettings settings, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(settings);
        await writeLock.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            paths.EnsureCreated();
            var temp = paths.SettingsFile + ".tmp";
            await File.WriteAllTextAsync(temp, JsonSerializer.Serialize(settings, Json), ct).ConfigureAwait(false);
            File.Move(temp, paths.SettingsFile, overwrite: true);
            Current = settings;
        }
        finally
        {
            writeLock.Release();
        }

        Changed?.Invoke(this, settings);
    }

    public void Dispose() => writeLock.Dispose();

    private AppSettings Load()
    {
        var file = paths.SettingsFile;
        if (!File.Exists(file))
        {
            return new AppSettings();
        }

        try
        {
            return JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(file), Json) ?? new AppSettings();
        }
        catch (JsonException ex)
        {
            LogUnreadable(ex.Message);
            File.Copy(file, file + ".bad", overwrite: true);
            return new AppSettings();
        }
    }

    [LoggerMessage(Level = LogLevel.Warning,
        Message = "settings.json could not be read ({Reason}); kept as settings.json.bad, starting from defaults")]
    private partial void LogUnreadable(string reason);
}
