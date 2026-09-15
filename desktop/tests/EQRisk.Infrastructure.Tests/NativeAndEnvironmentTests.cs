using System.Diagnostics;
using EQRisk.Infrastructure.Engine;
using EQRisk.Infrastructure.Native;

namespace EQRisk.Infrastructure.Tests;

/// <summary>Child-process cleanup: a job object ends the whole process tree.</summary>
public sealed class JobObjectTests
{
    [Fact]
    public void Terminating_the_job_ends_the_process_and_everything_it_started()
    {
        using var job = new JobObject();
        using var parent = Process.Start(Cmd.Run("ping -n 60 127.0.0.1 >nul"))!;
        job.Assign(parent);
        using var child = Processes.WaitForChild(parent.Id, "PING.EXE");

        job.Terminate();

        Assert.True(parent.WaitForExit(10_000), "the process kept running");
        Assert.True(child.WaitForExit(10_000), "its child outlived the job");
    }

    [Fact]
    public void Closing_the_handle_ends_them_too_as_it_would_if_the_app_crashed()
    {
        using var parent = Process.Start(Cmd.Run("ping -n 60 127.0.0.1 >nul"))!;
        var job = new JobObject();
        job.Assign(parent);
        using var child = Processes.WaitForChild(parent.Id, "PING.EXE");

        job.Dispose();

        Assert.True(parent.WaitForExit(10_000));
        Assert.True(child.WaitForExit(10_000));
    }
}

/// <summary>What an engine process starts with: UTF-8, no stray Python setup, keys only when given.</summary>
public sealed class EngineEnvironmentTests
{
    private static Dictionary<string, string?> Inherited() => new(StringComparer.OrdinalIgnoreCase)
    {
        ["PATH"] = @"C:\Windows",
        ["EODHD_API_KEY"] = "inherited-value",
        ["PYTHONPATH"] = @"C:\elsewhere",
        ["VIRTUAL_ENV"] = @"C:\other\venv",
        ["GITHUB_UPDATE_TOKEN"] = "app-token",
    };

    [Fact]
    public void A_process_without_keys_gets_none_even_inherited_ones()
    {
        var env = Inherited();
        EngineEnvironment.Prepare(env, keys: null, scrubInheritedKeys: true);
        Assert.False(env.ContainsKey("EODHD_API_KEY"));
        Assert.False(env.ContainsKey("PYTHONPATH"));
        Assert.False(env.ContainsKey("VIRTUAL_ENV"));
        Assert.Equal(@"C:\Windows", env["PATH"]);
        Assert.Equal("1", env["PYTHONUTF8"]);
        Assert.Equal("1", env["PYTHONUNBUFFERED"]);
    }

    [Fact]
    public void Vault_keys_override_inherited_ones_for_a_job_that_needs_them()
    {
        var env = Inherited();
        EngineEnvironment.Prepare(env, new Dictionary<string, string> { ["EODHD_API_KEY"] = "vault-value" },
            scrubInheritedKeys: false);
        Assert.Equal("vault-value", env["EODHD_API_KEY"]);
    }

    [Fact]
    public void The_app_s_update_token_never_reaches_the_engine()
    {
        var env = Inherited();
        EngineEnvironment.Prepare(env, keys: null, scrubInheritedKeys: false);
        Assert.False(env.ContainsKey("GITHUB_UPDATE_TOKEN"));
        Assert.Equal("inherited-value", env["EODHD_API_KEY"]);
        Assert.Throws<ArgumentException>(() => EngineEnvironment.Prepare(Inherited(),
            new Dictionary<string, string> { ["GITHUB_UPDATE_TOKEN"] = "x" }, scrubInheritedKeys: false));
    }
}
