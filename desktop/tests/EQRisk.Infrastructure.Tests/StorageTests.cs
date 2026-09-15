using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Engine;
using EQRisk.Infrastructure.Storage;
using Microsoft.Extensions.Logging.Abstractions;

namespace EQRisk.Infrastructure.Tests;

public sealed class SqliteJobStoreTests
{
    private static readonly DateTimeOffset T0 = new(2026, 9, 14, 22, 30, 0, TimeSpan.Zero);

    private static JobRecord Job(DateTimeOffset at, string label) =>
        new(0, JobKind.Validation, label, "validate", JobStatus.Running, JobTrigger.Schedule, at, null, null, "x.log");

    [Fact]
    public async Task Jobs_are_added_updated_and_listed_newest_first()
    {
        using var dir = new TempDir();
        var store = new SqliteJobStore(new AppPaths(dir.Path));
        var first = await store.AddAsync(Job(T0, "first"));
        var second = await store.AddAsync(Job(T0.AddMinutes(10), "second"));
        Assert.True(second.Id > first.Id);

        await store.UpdateAsync(first with { Status = JobStatus.Succeeded, FinishedAt = T0.AddMinutes(5), ExitCode = 0 });

        Assert.Equal(["second", "first"], (await store.RecentAsync(10)).Select(j => j.Label));
        var back = await store.GetAsync(first.Id);
        Assert.Equal(JobStatus.Succeeded, back!.Status);
        Assert.Equal(TimeSpan.FromMinutes(5), back.Duration);
        Assert.Equal(JobKind.Validation, back.Kind);
        Assert.Equal(JobTrigger.Schedule, back.Trigger);
        Assert.Equal(T0, back.StartedAt);
    }

    [Fact]
    public async Task Jobs_left_running_by_a_crash_become_interrupted()
    {
        using var dir = new TempDir();
        var store = new SqliteJobStore(new AppPaths(dir.Path));
        await store.AddAsync(Job(T0, "was running"));
        var done = await store.AddAsync(Job(T0, "finished"));
        await store.UpdateAsync(done with { Status = JobStatus.Failed, FinishedAt = T0, ExitCode = 2 });

        Assert.Equal(1, await store.MarkInterruptedAsync(T0.AddHours(1)));

        var statuses = (await store.RecentAsync(10)).ToDictionary(j => j.Label, j => j.Status);
        Assert.Equal(JobStatus.Interrupted, statuses["was running"]);
        Assert.Equal(JobStatus.Failed, statuses["finished"]);
    }

    [Fact]
    public async Task A_second_store_on_the_same_file_shares_the_history()
    {
        using var dir = new TempDir();
        await new SqliteJobStore(new AppPaths(dir.Path)).AddAsync(Job(T0, "from the window"));
        var other = new SqliteJobStore(new AppPaths(dir.Path));          // e.g. the scheduled headless run
        Assert.Single(await other.RecentAsync(5));
    }
}

public sealed class JsonSettingsStoreTests
{
    [Fact]
    public void Without_a_file_the_defaults_apply()
    {
        using var dir = new TempDir();
        using var store = new JsonSettingsStore(new AppPaths(dir.Path), NullLogger<JsonSettingsStore>.Instance);
        Assert.Null(store.Current.EngineRoot);
        Assert.True(store.Current.CloseToTray);
        Assert.Equal(new TimeOnly(6, 30), store.Current.ScheduleTime);
    }

    [Fact]
    public async Task Settings_survive_a_restart_and_announce_the_change()
    {
        using var dir = new TempDir();
        var paths = new AppPaths(dir.Path);
        using var store = new JsonSettingsStore(paths, NullLogger<JsonSettingsStore>.Instance);
        AppSettings? seen = null;
        store.Changed += (_, s) => seen = s;

        await store.SaveAsync(store.Current with { EngineRoot = @"C:\engine", ScheduleTime = new TimeOnly(7, 15) });

        Assert.Equal(@"C:\engine", seen?.EngineRoot);
        using var again = new JsonSettingsStore(paths, NullLogger<JsonSettingsStore>.Instance);
        Assert.Equal(@"C:\engine", again.Current.EngineRoot);
        Assert.Equal(new TimeOnly(7, 15), again.Current.ScheduleTime);
    }

    [Fact]
    public void A_damaged_file_is_kept_aside_and_the_defaults_apply()
    {
        using var dir = new TempDir();
        var paths = new AppPaths(dir.Path).EnsureCreated();
        File.WriteAllText(paths.SettingsFile, "{ not json");
        using var store = new JsonSettingsStore(paths, NullLogger<JsonSettingsStore>.Instance);
        Assert.Null(store.Current.EngineRoot);
        Assert.True(File.Exists(paths.SettingsFile + ".bad"));
    }
}

/// <summary>The DPAPI vault: values are only ever ciphertext at rest, and only engine keys go in.</summary>
public sealed class DpapiKeyVaultTests
{
    private const string Secret = "test-secret-value-8c1f2e";

    [Fact]
    public void A_stored_key_is_listed_by_name_and_read_back_only_for_a_process()
    {
        using var dir = new TempDir();
        var vault = new DpapiKeyVault(new AppPaths(dir.Path), NullLogger<DpapiKeyVault>.Instance);
        vault.Store("EODHD_API_KEY", Secret);

        Assert.Equal(new HashSet<string> { "EODHD_API_KEY" }, vault.Names);
        Assert.Equal(Secret, vault.ReadForProcess(["EODHD_API_KEY", "FRED_API_KEY"])["EODHD_API_KEY"]);
        Assert.False(vault.ReadForProcess(["FRED_API_KEY"]).ContainsKey("FRED_API_KEY"));
    }

    [Fact]
    public void The_file_holds_no_plaintext_and_survives_a_restart()
    {
        using var dir = new TempDir();
        var paths = new AppPaths(dir.Path);
        new DpapiKeyVault(paths, NullLogger<DpapiKeyVault>.Instance).Store("FRED_API_KEY", Secret);

        var onDisk = File.ReadAllText(paths.VaultFile);
        Assert.DoesNotContain(Secret, onDisk, StringComparison.Ordinal);
        Assert.DoesNotContain(Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(Secret)), onDisk,
            StringComparison.Ordinal);
        var again = new DpapiKeyVault(paths, NullLogger<DpapiKeyVault>.Instance);
        Assert.Equal(Secret, again.ReadForProcess(["FRED_API_KEY"])["FRED_API_KEY"]);
    }

    [Fact]
    public void Removing_a_key_forgets_it()
    {
        using var dir = new TempDir();
        var vault = new DpapiKeyVault(new AppPaths(dir.Path), NullLogger<DpapiKeyVault>.Instance);
        vault.Store("EODHD_API_KEY", Secret);
        Assert.True(vault.Remove("EODHD_API_KEY"));
        Assert.False(vault.Remove("EODHD_API_KEY"));
        Assert.Empty(vault.Names);
    }

    [Fact]
    public void Only_known_keys_can_be_stored()
    {
        using var dir = new TempDir();
        var vault = new DpapiKeyVault(new AppPaths(dir.Path), NullLogger<DpapiKeyVault>.Instance);
        Assert.Throws<ArgumentException>(() => vault.Store("PATH", "x"));
        Assert.Throws<ArgumentException>(() => vault.Store("EODHD_API_KEY", "  "));
    }

    [Fact]
    public void A_value_this_account_cannot_decrypt_is_skipped_not_thrown()
    {
        using var dir = new TempDir();
        var paths = new AppPaths(dir.Path).EnsureCreated();
        File.WriteAllText(paths.VaultFile,
            """{"Schema":1,"Entries":{"EODHD_API_KEY":"AQIDBAUGBwgJCgsMDQ4PEA=="}}""");
        var vault = new DpapiKeyVault(paths, NullLogger<DpapiKeyVault>.Instance);
        Assert.Contains("EODHD_API_KEY", vault.Names);
        Assert.Empty(vault.ReadForProcess(["EODHD_API_KEY"]));
    }
}

public sealed class EngineLocatorTests
{
    private static string MakeEngine(TempDir dir, string name = "eqrisk", bool venv = true)
    {
        var real = RepoEngine.PyProject();
        var toml = real is not null && name == "eqrisk"
            ? File.ReadAllText(real)                          // the real file: many sections Tomlyn must skip
            : $"[project]\nname = \"{name}\"\nversion = \"9.9.9\"\n";
        File.WriteAllText(dir.Combine("pyproject.toml"), toml);
        Directory.CreateDirectory(dir.Combine("configs"));
        if (venv)
        {
            Directory.CreateDirectory(dir.Combine(".venv", "Scripts"));
            File.WriteAllText(dir.Combine(".venv", "Scripts", "python.exe"), "");
        }

        return dir.Path;
    }

    [Fact]
    public void The_real_pyproject_is_read_with_tomlyn()
    {
        using var dir = new TempDir();
        var check = new EngineLocator().Check(MakeEngine(dir));
        Assert.True(check.Ok, check.Problem);
        Assert.Matches(@"^\d+\.\d+\.\d+", check.Engine!.Version);
        Assert.EndsWith(@".venv\Scripts\python.exe", check.Engine.PythonExe, StringComparison.Ordinal);
    }

    [Fact]
    public void Another_project_is_refused()
    {
        using var dir = new TempDir();
        var check = new EngineLocator().Check(MakeEngine(dir, name: "something-else"));
        Assert.False(check.Ok);
        Assert.Contains("another project", check.Problem, StringComparison.Ordinal);
    }

    [Fact]
    public void A_missing_python_environment_says_how_to_build_it()
    {
        using var dir = new TempDir();
        var check = new EngineLocator().Check(MakeEngine(dir, venv: false));
        Assert.Contains("uv sync", check.Problem, StringComparison.Ordinal);
    }

    [Fact]
    public void The_engine_is_found_by_walking_up_from_the_app()
    {
        using var dir = new TempDir();
        var root = MakeEngine(dir);
        var app = Directory.CreateDirectory(Path.Combine(root, "desktop", "src", "EQRisk.Desktop", "bin")).FullName;
        Assert.Equal(root, new EngineLocator().Locate(null, app).Engine?.Root);
    }

    [Fact]
    public void A_configured_folder_is_used_as_given_not_second_guessed()
    {
        using var dir = new TempDir();
        var app = Directory.CreateDirectory(Path.Combine(MakeEngine(dir), "bin")).FullName;
        var check = new EngineLocator().Locate(@"C:\no\such\folder", app);
        Assert.False(check.Ok);
        Assert.Contains("does not exist", check.Problem, StringComparison.Ordinal);
    }

    [Fact]
    public void A_folder_that_is_not_an_engine_is_explained() =>
        Assert.Contains("pyproject.toml", new EngineLocator().Check(Path.GetTempPath()).Problem, StringComparison.Ordinal);
}
