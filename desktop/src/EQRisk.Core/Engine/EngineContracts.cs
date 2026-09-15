using System.Text.Json;

namespace EQRisk.Core.Engine;

/// <summary>A located, usable engine: the CoreEquityRisk folder and the Python that runs it.</summary>
/// <param name="Root">The folder holding configs/, data/ and pyproject.toml.</param>
/// <param name="PythonExe">The project's own interpreter, .venv\Scripts\python.exe.</param>
/// <param name="Version">The engine's version from pyproject.toml.</param>
public sealed record EngineInfo(string Root, string PythonExe, string Version);

/// <summary>Why a folder is not a usable engine, or null when it is.</summary>
public sealed record EngineCheck(EngineInfo? Engine, string? Problem)
{
    public bool Ok => Engine is not null;

    public static EngineCheck Fail(string problem) => new(null, problem);
}

public interface IEngineLocator
{
    /// <summary>Check a folder: pyproject.toml names the eqrisk project and the virtual environment exists.</summary>
    EngineCheck Check(string root);

    /// <summary>The configured folder if it checks out, else one found by walking up from the app's
    /// own folder (a development build runs from inside the checkout).</summary>
    EngineCheck Locate(string? configuredRoot, string appDirectory);
}

public enum EngineConnectionState
{
    /// <summary>No engine folder is configured yet.</summary>
    NotConfigured,
    Starting,
    Ready,

    /// <summary>The engine process exited and is being started again.</summary>
    Restarting,

    /// <summary>The engine could not be started; <see cref="IEngineConnection.LastError"/> says why.</summary>
    Failed,
    Stopped,
}

/// <summary>The persistent, read-only engine process (<c>eqrisk serve</c>), one request at a time.</summary>
public interface IEngineConnection
{
    EngineConnectionState State { get; }

    string? LastError { get; }

    event EventHandler<EngineConnectionState>? StateChanged;

    /// <summary>Send one request and wait for its result. Throws <see cref="EngineException"/> when the
    /// engine answers with an error, <see cref="TimeoutException"/> when it does not answer in time.</summary>
    Task<JsonElement> CallAsync(string method, IReadOnlyDictionary<string, object?>? parameters, TimeSpan timeout,
        CancellationToken ct);
}

/// <summary>The engine answered, with an error: a missing table, an unknown date, a bad parameter.</summary>
public sealed class EngineException : Exception
{
    public EngineException()
    {
    }

    public EngineException(string message)
        : base(message)
    {
    }

    public EngineException(string message, Exception innerException)
        : base(message, innerException)
    {
    }

    public EngineException(string errorType, string message)
        : base(message)
    {
        ErrorType = errorType;
    }

    /// <summary>The Python exception type, such as LookupError or ValueError.</summary>
    public string ErrorType { get; } = "EngineError";
}

/// <summary>The engine process is not there: it could not start, or it exited mid-request. Reads
/// are idempotent, so a connection may retry them on a fresh process.</summary>
public sealed class EngineUnavailableException : Exception
{
    public EngineUnavailableException()
    {
    }

    public EngineUnavailableException(string message)
        : base(message)
    {
    }

    public EngineUnavailableException(string message, Exception innerException)
        : base(message, innerException)
    {
    }
}

/// <summary>Serves the exported static viewer on this computer only (127.0.0.1), with the engine's Python.</summary>
public interface IViewerServer
{
    bool IsServing { get; }

    Uri? Url { get; }

    Task<Uri> StartAsync(string folder, CancellationToken ct = default);

    void StopServing();
}

/// <summary>An eqrisk process this app did not start: a job from a terminal or another scheduler.</summary>
public sealed record ExternalEngineRun(int ProcessId, string CommandLine, DateTimeOffset? StartedAt);

public interface IEngineProcessProbe
{
    /// <summary>Running eqrisk jobs other than the given process ids (the app's own server and jobs).</summary>
    IReadOnlyList<ExternalEngineRun> FindJobs(IReadOnlyCollection<int> ownProcessIds);
}
