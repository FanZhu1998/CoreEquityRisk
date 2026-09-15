using EQRisk.Core.Jobs;
using EQRisk.Core.Keys;

namespace EQRisk.Core.Tests;

public sealed class DailyProgressTests
{
    [Fact]
    public void Before_anything_is_logged_the_first_step_is_current() =>
        Assert.Equal(0, DailyProgress.CurrentStep(""));

    [Theory]
    [InlineData("2026-09-14 waiting for vendor session=2026-09-14", 0)]
    [InlineData("waiting for vendor\ningested source=eodhd rows=503", 1)]
    [InlineData("ingested\nstaging through=2026-09-14", 2)]
    [InlineData("ingested\nstaging\ndescriptors sessions=2691 sids=1300", 3)]
    [InlineData("descriptors sessions=2691\nexposures first=2019-01-02", 4)]
    [InlineData("exposures first=2019-01-02\nnotify title=EQRisk", 5)]
    public void The_latest_step_reached_is_current(string log, int step) =>
        Assert.Equal(step, DailyProgress.CurrentStep(log));

    [Fact]
    public void There_are_six_steps_ending_with_the_gates()
    {
        Assert.Equal(6, DailyProgress.Steps.Count);
        Assert.Equal("Wait for data", DailyProgress.Steps[0].Name);
        Assert.Equal("Gates", DailyProgress.Steps[^1].Name);
    }
}

public sealed class KeyCatalogTests
{
    [Fact]
    public void The_vault_wins_then_env_then_missing()
    {
        var statuses = KeyCatalog.Resolve(
            inVault: new HashSet<string> { "EODHD_API_KEY" },
            inDotEnv: new HashSet<string> { "EODHD_API_KEY", "FRED_API_KEY" });
        KeySource Source(string env) => statuses.Single(s => s.Definition.Env == env).Source;
        Assert.Equal(KeySource.Vault, Source("EODHD_API_KEY"));
        Assert.Equal(KeySource.DotEnv, Source("FRED_API_KEY"));
        Assert.Equal(KeySource.Missing, Source("SEC_USER_AGENT"));
        Assert.Equal(["SEC_USER_AGENT"], KeyCatalog.MissingRequired(statuses));
    }

    [Fact]
    public void Optional_keys_are_never_reported_missing()
    {
        var none = KeyCatalog.Resolve(new HashSet<string>(), new HashSet<string>());
        Assert.Equal(["EODHD_API_KEY", "FRED_API_KEY", "SEC_USER_AGENT"], KeyCatalog.MissingRequired(none));
    }

    [Fact]
    public void The_update_token_is_the_app_s_own_and_never_an_engine_key()
    {
        Assert.False(KeyCatalog.IsEngineKey(KeyCatalog.UpdateToken.Env));
        Assert.True(KeyCatalog.IsEngineKey("EODHD_API_KEY"));
        Assert.DoesNotContain(KeyCatalog.Engine, k => k.Env == KeyCatalog.UpdateToken.Env);
    }

    [Fact]
    public void A_key_status_has_nowhere_to_hold_a_value()
    {
        var members = typeof(KeyStatus).GetProperties().Select(p => p.Name).Order().ToArray();
        Assert.Equal(["Configured", "Definition", "Source"], members);
    }
}
