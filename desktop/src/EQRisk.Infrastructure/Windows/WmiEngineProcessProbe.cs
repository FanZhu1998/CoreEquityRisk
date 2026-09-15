using System.Globalization;
using System.Management;
using System.Text.RegularExpressions;
using EQRisk.Core.Engine;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Windows;

/// <summary>
/// Finds eqrisk jobs this app did not start (a terminal, the scheduled task) through WMI
/// (Win32_Process), so the app never runs a second job on top of one and can say which is running.
/// The read server (<c>eqrisk serve</c>) and the workbench (<c>eqrisk ui</c>) are not jobs.
/// </summary>
public sealed partial class WmiEngineProcessProbe(ILogger<WmiEngineProcessProbe> log) : IEngineProcessProbe
{
    private const string Query =
        "SELECT ProcessId, CommandLine, CreationDate FROM Win32_Process " +
        "WHERE Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'eqrisk.exe'";

    private static readonly HashSet<string> JobCommands =
    [
        "run-daily", "ingest", "backfill", "validate", "export-site", "snapshot", "compact", "pull-overrides",
        "doctor",
    ];

    public IReadOnlyList<ExternalEngineRun> FindJobs(IReadOnlyCollection<int> ownProcessIds)
    {
        ArgumentNullException.ThrowIfNull(ownProcessIds);
        var found = new List<ExternalEngineRun>();
        try
        {
            using var searcher = new ManagementObjectSearcher(Query);
            using var results = searcher.Get();
            foreach (var item in results)
            {
                using (item)
                {
                    var pid = Convert.ToInt32(item["ProcessId"], CultureInfo.InvariantCulture);
                    if (ownProcessIds.Contains(pid) || item["CommandLine"] is not string command ||
                        JobCommand(command) is null)
                    {
                        continue;
                    }

                    DateTimeOffset? started = item["CreationDate"] is string created
                        ? new DateTimeOffset(ManagementDateTimeConverter.ToDateTime(created))
                        : null;
                    found.Add(new ExternalEngineRun(pid, command, started));
                }
            }
        }
        catch (Exception ex) when (ex is ManagementException or System.Runtime.InteropServices.COMException
                                       or UnauthorizedAccessException)
        {
            LogQueryFailed(ex.Message);
        }

        return found;
    }

    /// <summary>The eqrisk sub-command a process command line runs, when it is a job; otherwise null.</summary>
    internal static string? JobCommand(string commandLine)
    {
        var match = CommandPattern().Match(commandLine);
        if (!match.Success)
        {
            return null;
        }

        var command = match.Groups["cmd"].Value.ToLowerInvariant();
        return JobCommands.Contains(command) ? command : null;
    }

    [GeneratedRegex("""(?:^|[\\/\s"])eqrisk(?:\.exe|\.cli)?["']?\s+(?<cmd>[a-z][a-z-]*)""", RegexOptions.IgnoreCase)]
    private static partial Regex CommandPattern();

    [LoggerMessage(Level = LogLevel.Warning, Message = "Could not list processes through WMI: {Reason}")]
    private partial void LogQueryFailed(string reason);
}
