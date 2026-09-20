using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Presentation.Pages;

namespace EQRisk.Presentation.Tests;

// The factor-exposure comparison a Specific risk row opens: factors down, securities across.

public sealed class SecurityCompareTests
{
    private static readonly DateOnly On = Sample.Last;

    [Fact]
    public async Task A_security_becomes_a_column_and_the_factors_become_rows()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL") };
        var panel = new SecurityCompareViewModel(feed);

        Assert.True(await panel.AddAsync("aapl", On));

        Assert.True(panel.IsOpen);
        Assert.Equal(["AAPL"], panel.Chosen);
        Assert.Equal(["AAPL"], feed.LastCompared);
        Assert.NotNull(panel.Table);
        Assert.Equal(["Group", "Factor", "AAPL"], panel.Table!.Table!.Columns.Cast<System.Data.DataColumn>().Select(c => c.ColumnName));
        Assert.Null(panel.Problem);
    }

    [Fact]
    public async Task An_all_zero_industry_is_hidden_until_every_factor_is_asked_for()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL") };
        var panel = new SecurityCompareViewModel(feed);
        await panel.AddAsync("AAPL", On);

        Assert.Equal(3, panel.Table!.Count);                       // COUNTRY, TECH_HARDWARE, SIZE
        Assert.Contains("1 all-zero industries hidden", panel.Summary, StringComparison.Ordinal);

        panel.ShowEveryFactor = true;
        await panel.ReloadAsync(On);

        Assert.Equal(4, panel.Table!.Count);                       // UTILITIES joins them
    }

    [Fact]
    public async Task Five_is_the_cap_and_the_sixth_is_refused_with_a_reason()
    {
        string[] five = ["A", "B", "C", "D", "E"];
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures(five) };
        var panel = new SecurityCompareViewModel(feed);
        foreach (var t in five)
        {
            Assert.True(await panel.AddAsync(t, On));
        }

        Assert.True(panel.IsFull);
        Assert.False(await panel.AddAsync("F", On));
        Assert.Equal(five, panel.Chosen);                          // the sixth never reached the engine
        Assert.Equal(five, feed.LastCompared);
        Assert.Contains("Up to 5", panel.Problem, StringComparison.Ordinal);
    }

    [Fact]
    public async Task The_cap_is_the_engines_own_not_a_constant_here()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL") with { Max = 2 } };
        var panel = new SecurityCompareViewModel(feed);
        await panel.AddAsync("AAPL", On);

        Assert.Equal(2, panel.Max);
    }

    [Fact]
    public async Task Adding_the_same_name_twice_does_not_duplicate_a_column()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL") };
        var panel = new SecurityCompareViewModel(feed);
        await panel.AddAsync("AAPL", On);
        await panel.AddAsync("aapl", On);

        Assert.Equal(["AAPL"], panel.Chosen);
    }

    [Fact]
    public async Task A_name_the_model_does_not_carry_is_reported_not_kept()
    {
        var feed = new FakeFeed
        {
            SecurityExposures = Sample.SecurityExposures("AAPL") with { Unmatched = ["NOPE"] },
        };
        var panel = new SecurityCompareViewModel(feed);
        await panel.AddAsync("AAPL", On);
        await panel.AddAsync("NOPE", On);

        Assert.Equal(["AAPL"], panel.Chosen);
        Assert.Contains("NOPE", panel.Problem, StringComparison.Ordinal);
    }

    [Fact]
    public async Task Removing_the_last_name_closes_the_panel()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL") };
        var panel = new SecurityCompareViewModel(feed);
        await panel.AddAsync("AAPL", On);

        await panel.RemoveAsync("AAPL", On);

        Assert.False(panel.IsOpen);
        Assert.Null(panel.Table);
        Assert.Empty(panel.Chosen);
    }

    [Fact]
    public async Task An_engine_failure_is_reported_and_does_not_throw()
    {
        var feed = new FakeFeed { SecurityExposures = Sample.SecurityExposures("AAPL"), Failure = new InvalidOperationException("engine down") };
        var panel = new SecurityCompareViewModel(feed);

        await panel.AddAsync("AAPL", On);

        Assert.Equal("engine down", panel.Problem);
    }

    [Fact]
    public async Task The_specific_risk_page_opens_the_panel_for_an_activated_row()
    {
        var feed = new FakeFeed
        {
            Specific = Sample.Specific(),
            SecurityExposures = Sample.SecurityExposures("JPM"),
            Dates = Sample.Dates(),
        };
        var page = new SpecificRiskViewModel(feed, new WeakReferenceMessenger());
        await page.ReloadAsync();

        Assert.Null(page.Problem);
        Assert.False(page.Compare.IsOpen);

        await page.ShowSecurityCommand.ExecuteAsync("JPM");

        Assert.True(page.Compare.IsOpen);
        Assert.Equal(["JPM"], page.Compare.Chosen);
    }
}
