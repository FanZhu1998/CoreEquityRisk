using System.Globalization;
using EQRisk.Core.Jobs;
using Microsoft.Data.Sqlite;

namespace EQRisk.Infrastructure.Storage;

/// <summary>
/// The job history in SQLite (%LOCALAPPDATA%\EQRisk\eqrisk.db). Write-ahead logging lets the window
/// and a scheduled headless run record jobs at the same time.
/// </summary>
public sealed class SqliteJobStore : IJobStore
{
    private const string Schema = """
        CREATE TABLE IF NOT EXISTS jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            kind        TEXT    NOT NULL,
            label       TEXT    NOT NULL,
            arguments   TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            trigger     TEXT    NOT NULL,
            started_at  TEXT    NOT NULL,
            finished_at TEXT,
            exit_code   INTEGER,
            log_path    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS jobs_started ON jobs (started_at DESC);
        """;

    private const string Columns = "id, kind, label, arguments, status, trigger, started_at, finished_at, exit_code, log_path";

    private readonly string connectionString;

    public SqliteJobStore(AppPaths paths)
    {
        ArgumentNullException.ThrowIfNull(paths);
        paths.EnsureCreated();
        connectionString = new SqliteConnectionStringBuilder
        {
            DataSource = paths.Database,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Pooling = false,
            DefaultTimeout = 10,
        }.ToString();
        using var c = Open();
        Execute(c, "PRAGMA journal_mode = WAL;");
        Execute(c, Schema);
    }

    public async Task<JobRecord> AddAsync(JobRecord record, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(record);
        await using var c = Open();
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            INSERT INTO jobs (kind, label, arguments, status, trigger, started_at, finished_at, exit_code, log_path)
            VALUES ($kind, $label, $arguments, $status, $trigger, $started, $finished, $code, $log);
            SELECT last_insert_rowid();
            """;
        Bind(cmd, record);
        var id = Convert.ToInt64(await cmd.ExecuteScalarAsync(ct).ConfigureAwait(false), CultureInfo.InvariantCulture);
        return record with { Id = id };
    }

    public async Task UpdateAsync(JobRecord record, CancellationToken ct = default)
    {
        ArgumentNullException.ThrowIfNull(record);
        await using var c = Open();
        await using var cmd = c.CreateCommand();
        cmd.CommandText = """
            UPDATE jobs SET status = $status, finished_at = $finished, exit_code = $code WHERE id = $id;
            """;
        Bind(cmd, record);
        cmd.Parameters.AddWithValue("$id", record.Id);
        await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    public async Task<JobRecord?> GetAsync(long id, CancellationToken ct = default)
    {
        await using var c = Open();
        await using var cmd = c.CreateCommand();
        cmd.CommandText = $"SELECT {Columns} FROM jobs WHERE id = $id;";
        cmd.Parameters.AddWithValue("$id", id);
        await using var reader = await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false);
        return await reader.ReadAsync(ct).ConfigureAwait(false) ? Read(reader) : null;
    }

    public async Task<IReadOnlyList<JobRecord>> RecentAsync(int limit, CancellationToken ct = default)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(limit);
        await using var c = Open();
        await using var cmd = c.CreateCommand();
        cmd.CommandText = $"SELECT {Columns} FROM jobs ORDER BY started_at DESC, id DESC LIMIT $limit;";
        cmd.Parameters.AddWithValue("$limit", limit);
        await using var reader = await cmd.ExecuteReaderAsync(ct).ConfigureAwait(false);
        var list = new List<JobRecord>();
        while (await reader.ReadAsync(ct).ConfigureAwait(false))
        {
            list.Add(Read(reader));
        }

        return list;
    }

    public async Task<int> MarkInterruptedAsync(DateTimeOffset now, CancellationToken ct = default)
    {
        await using var c = Open();
        await using var cmd = c.CreateCommand();
        cmd.CommandText = "UPDATE jobs SET status = $interrupted, finished_at = $now WHERE status = $running;";
        cmd.Parameters.AddWithValue("$interrupted", nameof(JobStatus.Interrupted));
        cmd.Parameters.AddWithValue("$running", nameof(JobStatus.Running));
        cmd.Parameters.AddWithValue("$now", Iso(now));
        return await cmd.ExecuteNonQueryAsync(ct).ConfigureAwait(false);
    }

    private SqliteConnection Open()
    {
        var c = new SqliteConnection(connectionString);
        c.Open();
        Execute(c, "PRAGMA busy_timeout = 10000;");
        return c;
    }

    private static void Execute(SqliteConnection c, string sql)
    {
        using var cmd = c.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    private static void Bind(SqliteCommand cmd, JobRecord r)
    {
        cmd.Parameters.AddWithValue("$kind", r.Kind.ToString());
        cmd.Parameters.AddWithValue("$label", r.Label);
        cmd.Parameters.AddWithValue("$arguments", r.Arguments);
        cmd.Parameters.AddWithValue("$status", r.Status.ToString());
        cmd.Parameters.AddWithValue("$trigger", r.Trigger.ToString());
        cmd.Parameters.AddWithValue("$started", Iso(r.StartedAt));
        cmd.Parameters.AddWithValue("$finished", r.FinishedAt is { } f ? Iso(f) : DBNull.Value);
        cmd.Parameters.AddWithValue("$code", r.ExitCode is { } e ? e : DBNull.Value);
        cmd.Parameters.AddWithValue("$log", r.LogPath);
    }

    private static JobRecord Read(SqliteDataReader r) => new(
        Id: r.GetInt64(0),
        Kind: Enum.TryParse<JobKind>(r.GetString(1), out var kind) ? kind : JobKind.DailyUpdate,
        Label: r.GetString(2),
        Arguments: r.GetString(3),
        Status: Enum.TryParse<JobStatus>(r.GetString(4), out var status) ? status : JobStatus.Interrupted,
        Trigger: Enum.TryParse<JobTrigger>(r.GetString(5), out var trigger) ? trigger : JobTrigger.App,
        StartedAt: Parse(r.GetString(6)),
        FinishedAt: r.IsDBNull(7) ? null : Parse(r.GetString(7)),
        ExitCode: r.IsDBNull(8) ? null : r.GetInt32(8),
        LogPath: r.GetString(9));

    private static string Iso(DateTimeOffset t) => t.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);

    private static DateTimeOffset Parse(string s) =>
        DateTimeOffset.Parse(s, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
}
