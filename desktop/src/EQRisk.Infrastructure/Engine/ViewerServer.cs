using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using EQRisk.Core.Engine;
using EQRisk.Core.Settings;
using EQRisk.Infrastructure.Native;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Engine;

/// <summary>
/// Serves the exported static viewer with the engine's Python (<c>python -m http.server</c>), bound to
/// 127.0.0.1 so nothing on the network can reach it. The server holds no keys and lives in a job
/// object, so it ends with the app.
/// </summary>
public sealed partial class ViewerServer(IEngineLocator locator, ISettingsStore settings, ILogger<ViewerServer> log,
    string appDirectory) : IViewerServer, IDisposable
{
    public const int FirstPort = 8000;
    private const int PortsTried = 20;
    private static readonly TimeSpan ReadyWithin = TimeSpan.FromSeconds(15);

    private Process? process;
    private JobObject? job;

    public Uri? Url { get; private set; }

    public bool IsServing => process is { HasExited: false };

    public async Task<Uri> StartAsync(string folder, CancellationToken ct = default)
    {
        if (IsServing && Url is { } running)
        {
            return running;
        }

        StopServing();
        if (!Directory.Exists(folder))
        {
            throw new InvalidOperationException($"{folder} does not exist. Export the viewer first.");
        }

        var check = locator.Locate(settings.Current.EngineRoot, appDirectory);
        if (!check.Ok)
        {
            throw new InvalidOperationException(check.Problem);
        }

        var port = FreePort();
        var psi = new ProcessStartInfo(check.Engine!.PythonExe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = folder,
        };
        foreach (var arg in (string[])["-m", "http.server", port.ToString(CultureInfo.InvariantCulture), "--bind",
                     "127.0.0.1", "--directory", folder])
        {
            psi.ArgumentList.Add(arg);
        }

        EngineEnvironment.Prepare(psi.Environment, keys: null, scrubInheritedKeys: true);
        var p = Process.Start(psi) ?? throw new InvalidOperationException("The viewer server did not start.");
        var j = new JobObject();
        j.Assign(p);
        ProcessPriority.Normal(p);
        process = p;
        job = j;

        var url = new Uri($"http://127.0.0.1:{port}/");
        var deadline = DateTime.UtcNow + ReadyWithin;
        while (DateTime.UtcNow < deadline)
        {
            if (p.HasExited)
            {
                StopServing();
                throw new InvalidOperationException("The viewer server exited as it started.");
            }

            if (await AcceptsAsync(port, ct).ConfigureAwait(false))
            {
                Url = url;
                LogServing(folder, port);
                return url;
            }

            await Task.Delay(200, ct).ConfigureAwait(false);
        }

        StopServing();
        throw new TimeoutException("The viewer server did not answer in time.");
    }

    public void StopServing()
    {
        job?.Terminate();
        job?.Dispose();
        process?.Dispose();
        job = null;
        process = null;
        Url = null;
    }

    public void Dispose() => StopServing();

    private static int FreePort()
    {
        for (var port = FirstPort; port < FirstPort + PortsTried; port++)
        {
            try
            {
                var listener = new TcpListener(IPAddress.Loopback, port);
                listener.Start();
                listener.Stop();
                return port;
            }
            catch (SocketException)
            {
                // Taken; try the next.
            }
        }

        throw new InvalidOperationException($"Ports {FirstPort} to {FirstPort + PortsTried - 1} are all in use.");
    }

    private static async Task<bool> AcceptsAsync(int port, CancellationToken ct)
    {
        using var client = new TcpClient();
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
            timeout.CancelAfter(TimeSpan.FromMilliseconds(500));
            await client.ConnectAsync(IPAddress.Loopback, port, timeout.Token).ConfigureAwait(false);
            return true;
        }
        catch (Exception ex) when (ex is SocketException or OperationCanceledException)
        {
            return false;
        }
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Serving the viewer in {Folder} on 127.0.0.1:{Port}")]
    private partial void LogServing(string folder, int port);
}
