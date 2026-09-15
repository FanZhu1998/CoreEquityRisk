using System.Diagnostics;
using EQRisk.Core.Engine;
using EQRisk.Core.Feed;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Engine;
using Microsoft.Extensions.Logging.Abstractions;

namespace EQRisk.Infrastructure.Tests;

/// <summary>One real `eqrisk serve` process for the tests below, when this checkout has a built engine
/// and model data; otherwise they are skipped.</summary>
public sealed class EngineFixture : IAsyncLifetime
{
    public EngineInfo? Engine { get; } = RepoEngine.Find() is { } e && RepoEngine.HasModelData(e) ? e : null;

    public EngineConnection? Connection { get; private set; }

    public EngineFeed Feed => new(Connection!);

    public async ValueTask InitializeAsync()
    {
        if (Engine is null)
        {
            return;
        }

        Connection = new EngineConnection(new EngineLocator(), new FixedSettings(new AppSettings { EngineRoot = Engine.Root }),
            NullLogger<EngineConnection>.Instance, AppContext.BaseDirectory);
        await Connection.StartAsync(CancellationToken.None);
    }

    public async ValueTask DisposeAsync()
    {
        if (Connection is not null)
        {
            await Connection.DisposeAsync();
        }
    }
}

[CollectionDefinition(nameof(RealEngine))]
public sealed class RealEngine : ICollectionFixture<EngineFixture>;

/// <summary>The cross-language contract: every read the app makes, against the real engine and data.</summary>
[Collection(nameof(RealEngine))]
public sealed class EngineConnectionTests(EngineFixture fx)
{
    private void RequireEngine() =>
        Assert.SkipWhen(fx.Engine is null, "no built engine with model data in this checkout");

    [Fact]
    public async Task Every_read_deserializes_from_the_real_engine()
    {
        RequireEngine();
        var feed = fx.Feed;
        Assert.Equal(EngineConnectionState.Ready, fx.Connection!.State);
        Assert.Equal(1, fx.Connection.Hello!.Protocol);

        var status = await feed.StatusAsync();
        Assert.NotNull(status.Last);
        Assert.Contains(status.Keys, k => k.Env == "EODHD_API_KEY");

        var day = await feed.DayAsync();
        Assert.Equal(status.Last, day.Date);
        Assert.True(day.Moves.Count >= 30);
        Assert.Contains(day.Moves, m => m.Group == "style");

        var history = await feed.HistoryAsync(1);
        Assert.Equal(history.VraDates.Count, history.LambdaF.Count);
        Assert.True(history.Cum.ContainsKey("SIZE"));

        var risk = await feed.FactorRiskAsync();
        Assert.Equal(risk.Factors.Count, risk.Corr.Count);
        Assert.All(risk.Factors, f => Assert.InRange(f.VolAnn!.Value, 0.0, 1.0));

        var exposures = await feed.ExposuresAsync();
        Assert.True(exposures.Rows.Count > 400);
        Assert.NotNull(exposures.Rows[0].Style("SIZE"));

        var specific = await feed.SpecificAsync();
        Assert.True(specific.Rows.Count > 400);

        var inventory = await feed.InventoryAsync();
        Assert.True(inventory.EodSessions > 1000);

        var exceptions = await feed.ExceptionsAsync(10);
        Assert.Equal(exceptions.Total, exceptions.Counts.Sum(c => c.Count));

        var runs = await feed.RunsAsync(5);
        Assert.True(runs.Runs.Count <= 5);

        var config = await feed.ConfigAsync();
        Assert.Equal(status.ModelId, config.ModelId);

        var dates = await feed.DatesAsync();
        Assert.Contains(dates.Latest!.Value, dates.Dates);

        var validation = await feed.ValidationAsync();
        Assert.True(validation is null || validation.Scorecard.Count > 0);
    }

    [Fact]
    public async Task Portfolio_analysis_and_optimization_run_in_the_engine()
    {
        RequireEngine();
        var report = await fx.Feed.PortfolioAsync([new Holding("AAPL", 0.5), new Holding("MSFT", 0.5), new Holding("NOPE", 0.1)]);
        Assert.Equal(["NOPE"], report.Unmatched);
        Assert.InRange(report.SigmaAnn, 0.05, 1.0);

        var outcome = await fx.Feed.OptimizeAsync(new OptimizeRequest("factor", 0.03, 0.10, 0.02, 0.05, 1.0));
        Assert.True(outcome.Ok, outcome.Status);
        Assert.True(outcome.NamesHeld > 20);
        Assert.True(outcome.SigmaAnn <= 0.031);
    }

    [Fact]
    public async Task An_engine_error_arrives_typed_and_the_connection_keeps_working()
    {
        RequireEngine();
        var ex = await Assert.ThrowsAsync<EngineException>(() => fx.Feed.FactorRiskAsync(new DateOnly(1990, 1, 2)));
        Assert.Equal("LookupError", ex.ErrorType);
        Assert.NotNull(await fx.Feed.StatusAsync());
    }

    [Fact]
    public async Task A_killed_engine_is_started_again_behind_the_next_call()
    {
        RequireEngine();
        await fx.Feed.StatusAsync();
        var first = fx.Connection!.ProcessId!.Value;
        using (var p = Process.GetProcessById(first))
        {
            p.Kill();
            await p.WaitForExitAsync();
        }

        var status = await fx.Feed.StatusAsync();          // lost its process; Polly retries on a fresh one

        Assert.NotNull(status.Last);
        Assert.NotEqual(first, fx.Connection.ProcessId);
        Assert.Equal(EngineConnectionState.Ready, fx.Connection.State);
    }
}
