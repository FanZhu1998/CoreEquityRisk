using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;
using EQRisk.Core.Engine;

namespace EQRisk.Core.Feed;

/// <summary>JSON settings for the engine protocol: snake_case names, strict numbers, ISO dates.</summary>
public static class FeedJson
{
    public static JsonSerializerOptions Options { get; } = Create();

    private static JsonSerializerOptions Create()
    {
        var options = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
            PropertyNameCaseInsensitive = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        };
        options.MakeReadOnly(populateMissingResolver: true);
        return options;
    }
}

/// <summary>Everything the app reads from the model, typed. One method per engine read.</summary>
public interface IEngineFeed
{
    Task<ModelStatus> StatusAsync(CancellationToken ct = default);

    Task<IReadOnlyList<KeyPresence>> KeysAsync(CancellationToken ct = default);

    Task<ModelDates> DatesAsync(CancellationToken ct = default);

    Task<DayInfo> DayAsync(DateOnly? session = null, CancellationToken ct = default);

    Task<HistoryInfo> HistoryAsync(int years, DateOnly? asOf = null, CancellationToken ct = default);

    Task<FactorRiskInfo> FactorRiskAsync(DateOnly? asOf = null, CancellationToken ct = default);

    Task<ExposuresInfo> ExposuresAsync(DateOnly? asOf = null, CancellationToken ct = default);

    Task<SpecificInfo> SpecificAsync(DateOnly? asOf = null, CancellationToken ct = default);

    Task<ValidationReport?> ValidationAsync(CancellationToken ct = default);

    Task<Inventory> InventoryAsync(CancellationToken ct = default);

    Task<ExceptionsInfo> ExceptionsAsync(int limit, CancellationToken ct = default);

    Task<RunsInfo> RunsAsync(int limit, CancellationToken ct = default);

    Task<EngineConfig> ConfigAsync(CancellationToken ct = default);

    Task<OutputsInfo> OutputsAsync(CancellationToken ct = default);

    Task<PortfolioReport> PortfolioAsync(IReadOnlyList<Holding> holdings, DateOnly? asOf = null,
        CancellationToken ct = default);

    Task<OptimizeOutcome> OptimizeAsync(OptimizeRequest request, DateOnly? asOf = null, CancellationToken ct = default);

    /// <summary>Drop the engine's caches, after a job changed the model.</summary>
    Task RefreshAsync(CancellationToken ct = default);
}

/// <summary>The typed feed over any <see cref="IEngineConnection"/>. Pure translation: no I/O of its own.</summary>
public sealed class EngineFeed(IEngineConnection connection) : IEngineFeed
{
    /// <summary>Reads are milliseconds once warm; the first read of a table can take a few seconds.</summary>
    public static readonly TimeSpan ReadTimeout = TimeSpan.FromSeconds(90);

    /// <summary>The optimizer solves inside the engine.</summary>
    public static readonly TimeSpan SolveTimeout = TimeSpan.FromMinutes(5);

    public Task<ModelStatus> StatusAsync(CancellationToken ct = default) => Call<ModelStatus>("status", null, ct);

    public Task<IReadOnlyList<KeyPresence>> KeysAsync(CancellationToken ct = default) =>
        Call<IReadOnlyList<KeyPresence>>("keys", null, ct);

    public Task<ModelDates> DatesAsync(CancellationToken ct = default) => Call<ModelDates>("dates", null, ct);

    public Task<DayInfo> DayAsync(DateOnly? session = null, CancellationToken ct = default) =>
        Call<DayInfo>("day", Dated(session), ct);

    public Task<HistoryInfo> HistoryAsync(int years, DateOnly? asOf = null, CancellationToken ct = default)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(years);
        var p = Dated(asOf) ?? [];
        p["years"] = years;
        return Call<HistoryInfo>("history", p, ct);
    }

    public Task<FactorRiskInfo> FactorRiskAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        Call<FactorRiskInfo>("factor_risk", Dated(asOf), ct);

    public Task<ExposuresInfo> ExposuresAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        Call<ExposuresInfo>("exposures", Dated(asOf), ct);

    public Task<SpecificInfo> SpecificAsync(DateOnly? asOf = null, CancellationToken ct = default) =>
        Call<SpecificInfo>("specific", Dated(asOf), ct);

    public async Task<ValidationReport?> ValidationAsync(CancellationToken ct = default)
    {
        var result = await connection.CallAsync("validation", null, ReadTimeout, ct).ConfigureAwait(false);
        return result.ValueKind == JsonValueKind.Null ? null : result.Deserialize<ValidationReport>(FeedJson.Options);
    }

    public Task<Inventory> InventoryAsync(CancellationToken ct = default) => Call<Inventory>("inventory", null, ct);

    public Task<ExceptionsInfo> ExceptionsAsync(int limit, CancellationToken ct = default) =>
        Call<ExceptionsInfo>("exceptions", new() { ["limit"] = limit }, ct);

    public Task<RunsInfo> RunsAsync(int limit, CancellationToken ct = default) =>
        Call<RunsInfo>("runs", new() { ["limit"] = limit }, ct);

    public Task<EngineConfig> ConfigAsync(CancellationToken ct = default) => Call<EngineConfig>("config", null, ct);

    public Task<OutputsInfo> OutputsAsync(CancellationToken ct = default) => Call<OutputsInfo>("outputs", null, ct);

    public Task<PortfolioReport> PortfolioAsync(IReadOnlyList<Holding> holdings, DateOnly? asOf = null,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(holdings);
        var p = Dated(asOf) ?? [];
        p["holdings"] = holdings;
        return Call<PortfolioReport>("portfolio", p, ct);
    }

    public Task<OptimizeOutcome> OptimizeAsync(OptimizeRequest request, DateOnly? asOf = null,
        CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(request);
        var p = Dated(asOf) ?? [];
        p["method"] = request.Method;
        p["te_max_ann"] = request.TeMaxAnn;
        p["style_band"] = request.StyleBand;
        p["ind_band"] = request.IndBand;
        p["w_max"] = request.WMax;
        p["turnover_max"] = request.TurnoverMax;
        p["alpha_factor"] = request.AlphaFactor;
        p["alpha_per_unit"] = request.AlphaPerUnit;
        return Call<OptimizeOutcome>("optimize", p, ct, SolveTimeout);
    }

    public Task RefreshAsync(CancellationToken ct = default) => connection.CallAsync("refresh", null, ReadTimeout, ct);

    private async Task<T> Call<T>(string method, Dictionary<string, object?>? parameters, CancellationToken ct,
        TimeSpan? timeout = null)
    {
        var result = await connection.CallAsync(method, parameters, timeout ?? ReadTimeout, ct).ConfigureAwait(false);
        return result.Deserialize<T>(FeedJson.Options)
               ?? throw new EngineException("EmptyResult", $"the engine returned nothing for {method}");
    }

    private static Dictionary<string, object?>? Dated(DateOnly? date) =>
        date is { } d ? new() { ["date"] = d.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture) } : null;
}
