using System.Text.Json;
using EQRisk.Core.Engine;
using EQRisk.Core.Feed;

namespace EQRisk.Core.Tests;

/// <summary>The engine's JSON (shapes copied from `eqrisk serve` on the development data) into the records.</summary>
public sealed class FeedJsonTests
{
    private static T Parse<T>(string json) => JsonSerializer.Deserialize<T>(json, FeedJson.Options)!;

    [Fact]
    public void Status_with_its_last_run_and_key_presence()
    {
        var s = Parse<ModelStatus>("""
            {"model_id":"us_lc_v1","engine_version":"0.1.0","now_et":"2026-09-14T08:49","last":"2026-09-11",
             "target":"2026-09-11","pending":[],"next_session":"2026-09-14","ready_after_et":"18:30",
             "latest_good":"2026-09-11","daily_runs":1,
             "last_run":{"run_id":"20260912T030700-de5191b4","command":"run-daily --date 2026-09-11",
               "as_of":"2026-09-11","status":"OK","started_at":"2026-09-12T03:07:00.884053+00:00",
               "finished_at":"2026-09-12T03:07:00.915287+00:00","minutes":5.1095,
               "gates":[{"gate":"data_freshness","level":"FAIL","ok":true,"detail":"503/503 coverage names price"}],
               "fail_gates":[],"warn_gates":["vra"],"config_hash":"e88ae1a72255d06c","git_sha":"93b71c9dc69d",
               "git_dirty":true},
             "keys":[{"env":"EODHD_API_KEY","present":true},{"env":"FRED_API_KEY","present":false}]}
            """);
        Assert.Equal(new DateOnly(2026, 9, 11), s.Last);
        Assert.True(s.UpToDate);
        Assert.Equal("18:30", s.ReadyAfterEt);
        Assert.Equal(5.1095, s.LastRun!.Minutes);
        Assert.Equal(TimeSpan.Zero, s.LastRun.StartedAt.Offset);
        Assert.Equal(["vra"], s.LastRun.WarnGates);
        Assert.True(s.LastRun.Gates[0].Ok);
        Assert.Equal(new HashSet<string> { "EODHD_API_KEY" }, s.KeysInDotEnv);
    }

    [Fact]
    public void Day_with_regime_multipliers_and_missing_values()
    {
        var d = Parse<DayInfo>("""
            {"date":"2026-09-11","country":0.0089,"r2":0.28,"n":498,"cond":20.29,"fit_status":"ok",
             "lambda_F":1.1067,"lambda_S":1.0814,
             "moves":[{"factor":"BANKS","group":"industry","f":-0.0022,"t_stat":-0.56,"f_over_sigma":-0.249},
                      {"factor":"SIZE","group":"style","f":null,"t_stat":null,"f_over_sigma":null}]}
            """);
        Assert.Equal(1.1067, d.LambdaF);
        Assert.Equal(1.0814, d.LambdaS);
        Assert.Equal(498, d.N);
        Assert.Equal(-0.56, d.Moves[0].TStat);
        Assert.Equal(-0.249, d.Moves[0].FOverSigma);
        Assert.Null(d.Moves[1].F);
    }

    [Fact]
    public void History_keeps_factor_names_as_given()
    {
        var h = Parse<HistoryInfo>("""
            {"as_of":"2026-09-11","years":1,"dates":["2025-09-11","2025-09-12"],
             "cum":{"COUNTRY":[0.0077,null],"SIZE":[0.001,0.002]},"vol_dates":["2025-09-11"],
             "vol_ann":{"COUNTRY":[0.1593]},"vra_dates":["2025-09-11"],"lambda_F":[1.02],"lambda_S":[0.98],
             "cs_vol":[0.011],"r2_dates":["2025-09-11"],"r2":[0.31]}
            """);
        Assert.Equal(2, h.Cum["COUNTRY"].Count);
        Assert.Null(h.Cum["COUNTRY"][1]);
        Assert.Equal(0.1593, h.VolAnn["COUNTRY"][0]);
        Assert.Equal(1.02, h.LambdaF[0]);
        Assert.Equal(0.31, h.R2[0]);
        Assert.Equal(new DateOnly(2025, 9, 11), h.R2Dates[0]);
    }

    [Fact]
    public void Exposure_styles_arrive_by_name()
    {
        var e = Parse<ExposuresInfo>("""
            {"as_of":"2026-09-11","styles":["BETA","SIZE"],
             "rows":[{"sid":1,"ticker":"A","industry":"PHARMA_BIOTECH","in_estu":true,"BETA":-0.26,"SIZE":-2.19}]}
            """);
        var row = e.Rows[0];
        Assert.Equal(-2.19, row.Style("SIZE"));
        Assert.Equal(-0.26, row.Style("BETA"));
        Assert.Null(row.Style("MOMENTUM"));
        Assert.True(row.InEstu);
    }

    [Fact]
    public void Validation_tables_with_spaced_column_names_and_booleans()
    {
        var v = Parse<ValidationReport>("""
            {"start":"2019-01-02","end":"2026-09-11","periods":92,
             "scorecard":[{"area":"Operations","criterion":"incremental daily run < 10 m","value":"5.1 min","status":"PASS"}],
             "external":[{"area":"Ken French","criterion":"COUNTRY~Mkt-RF >= 0.95","value":"0.996","status":"PASS"}],
             "factor":[{"portfolio":"COUNTRY","n":92.0,"bias":1.0023,"band":0.1474,"inside":true,"mrad":0.211,"qlike":1.98}],
             "eigen":[{"k":0.0,"n":92.0,"bias_before":1.54,"bias_after":1.14}],
             "specific_deciles":[{"grouping":"size decile","decile":1.0,"full stack":1.054,"time series only":1.098}],
             "market":[],"dir":"C:\\reports\\v","figures":["eigen_smile.png"]}
            """);
        Assert.Equal(92, v.Periods);
        Assert.True(v.Factor[0].Inside);
        Assert.Equal(1.54, v.Eigen[0].BiasBefore);
        Assert.Equal(1.054, v.SpecificDeciles[0].FullStack);
        Assert.Equal(1.098, v.SpecificDeciles[0].TimeSeriesOnly);
        Assert.Equal("PASS", v.External[0].Status);
    }

    [Fact]
    public void An_infeasible_optimization_carries_only_its_status()
    {
        var o = Parse<OptimizeOutcome>("""{"as_of":"2026-09-11","ok":false,"status":"infeasible"}""");
        Assert.False(o.Ok);
        Assert.Equal("infeasible", o.Status);
        Assert.Null(o.Holdings);
    }
}

/// <summary>The typed feed sends the right request and translates the answer.</summary>
public sealed class EngineFeedTests
{
    [Fact]
    public async Task A_dated_read_sends_an_iso_date()
    {
        var c = new FakeConnection("""{"date":"2026-09-11","country":null,"r2":null,"n":null,"cond":null,"fit_status":null,"lambda_F":null,"lambda_S":null,"moves":[]}""");
        await new EngineFeed(c).DayAsync(new DateOnly(2026, 9, 11));
        Assert.Equal("day", c.Method);
        Assert.Equal("2026-09-11", c.Params!["date"]);
    }

    [Fact]
    public async Task An_undated_read_sends_no_parameters()
    {
        var c = new FakeConnection("""{"dates":[],"latest":null}""");
        await new EngineFeed(c).DatesAsync();
        Assert.Equal("dates", c.Method);
        Assert.Null(c.Params);
    }

    [Fact]
    public async Task Holdings_and_optimizer_settings_go_out_in_snake_case()
    {
        var c = new FakeConnection("""{"as_of":"2026-09-11","ok":false,"status":"infeasible"}""");
        var feed = new EngineFeed(c);
        await feed.OptimizeAsync(new OptimizeRequest("factor", 0.03, 0.1, 0.02, 0.05, 1.0, "MOMENTUM", 0.01));
        var sent = JsonSerializer.Serialize(c.Params, FeedJson.Options);
        foreach (var name in new[] { "te_max_ann", "style_band", "ind_band", "w_max", "turnover_max", "alpha_factor", "alpha_per_unit" })
        {
            Assert.Contains($"\"{name}\"", sent, StringComparison.Ordinal);
        }

        c.Reply = """{"as_of":"2026-09-11","active":true,"sigma_ann":0.2,"factor_share":0.6,"specific_share":0.4,"beta":1.0,"groups":[],"factors":[],"assets":[],"unmatched":[],"matched":2}""";
        await feed.PortfolioAsync([new Holding("AAPL", 0.5, 0.4), new Holding("MSFT", 0.5)]);
        sent = JsonSerializer.Serialize(c.Params, FeedJson.Options);
        Assert.Contains("\"bench_weight\":0.4", sent, StringComparison.Ordinal);
        Assert.DoesNotContain("BenchWeight", sent, StringComparison.Ordinal);
    }

    [Fact]
    public async Task No_validation_report_is_null_not_an_error()
    {
        Assert.Null(await new EngineFeed(new FakeConnection("null")).ValidationAsync());
    }

    [Fact]
    public async Task A_null_answer_for_a_required_read_is_an_engine_error()
    {
        var ex = await Assert.ThrowsAsync<EngineException>(() => new EngineFeed(new FakeConnection("null")).StatusAsync());
        Assert.Equal("EmptyResult", ex.ErrorType);
    }

    private sealed class FakeConnection(string reply) : IEngineConnection
    {
        public string Reply { get; set; } = reply;

        public string? Method { get; private set; }

        public IReadOnlyDictionary<string, object?>? Params { get; private set; }

        public EngineConnectionState State => EngineConnectionState.Ready;

        public string? LastError => null;

        public event EventHandler<EngineConnectionState>? StateChanged
        {
            add { }
            remove { }
        }

        public Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?>? parameters,
            TimeSpan timeout, CancellationToken ct)
        {
            Method = method;
            Params = parameters;
            using var doc = JsonDocument.Parse(Reply);
            return Task.FromResult(doc.RootElement.Clone());
        }
    }
}
