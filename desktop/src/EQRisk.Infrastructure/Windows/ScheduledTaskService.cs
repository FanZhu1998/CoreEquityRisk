using System.Globalization;
using System.Runtime.InteropServices;
using EQRisk.Core.Settings;
using Microsoft.Extensions.Logging;

namespace EQRisk.Infrastructure.Windows;

/// <summary>
/// The daily Windows scheduled task, through the Task Scheduler COM API (Schedule.Service). It runs as
/// the signed-in user, so the app's DPAPI vault can be read, and starts as soon as possible after a
/// missed time (the PC was asleep).
/// </summary>
public sealed partial class ScheduledTaskService(ILogger<ScheduledTaskService> log, string taskName = "EQRisk Daily")
    : IScheduledTaskService
{
    // Task Scheduler constants (taskschd.h).
    private const int TriggerDaily = 2;
    private const int ActionExec = 0;
    private const int CreateOrUpdate = 6;
    private const int LogonInteractiveToken = 3;
    private const int InstancesIgnoreNew = 2;
    private const int NotFound = unchecked((int)0x80070002);
    private static readonly DateTime NeverRun = new(1999, 12, 31);

    public string TaskName => taskName;

    public Task<ScheduledTaskInfo?> GetAsync(CancellationToken ct = default) => Task.Run(Get, ct);

    public Task RegisterAsync(TimeOnly at, string exePath, string arguments, string workingDirectory,
        CancellationToken ct = default) =>
        Task.Run(() => Register(at, exePath, arguments, workingDirectory), ct);

    public Task RunNowAsync(CancellationToken ct = default) => Task.Run(() => WithTask(task => task.Run(null)), ct);

    public Task RemoveAsync(CancellationToken ct = default) => Task.Run(() =>
    {
        WithService(service =>
        {
            dynamic folder = service.GetFolder("\\");
            try
            {
                folder.DeleteTask(TaskName, 0);
                LogRemoved(TaskName);
            }
            catch (Exception ex) when (IsNotFound(ex))
            {
                // Already gone.
            }
        });
    }, ct);

    internal static string StateName(int state) => state switch
    {
        1 => "Disabled",
        2 => "Queued",
        3 => "Ready",
        4 => "Running",
        _ => "Unknown",
    };

    private ScheduledTaskInfo? Get()
    {
        ScheduledTaskInfo? info = null;
        WithService(service =>
        {
            dynamic folder = service.GetFolder("\\");
            dynamic task;
            try
            {
                task = folder.GetTask(TaskName);
            }
            catch (Exception ex) when (IsNotFound(ex))
            {
                return;
            }

            string? command = null;
            foreach (dynamic action in task.Definition.Actions)
            {
                if ((int)action.Type == ActionExec)
                {
                    command = $"\"{action.Path}\" {action.Arguments}".Trim();
                }
            }

            DateTime next = task.NextRunTime;
            DateTime last = task.LastRunTime;
            info = new ScheduledTaskInfo(StateName((int)task.State), Valid(next), Valid(last),
                (int)task.LastTaskResult, command);
        });
        return info;
    }

    private void Register(TimeOnly at, string exePath, string arguments, string workingDirectory)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(exePath);
        WithService(service =>
        {
            dynamic definition = service.NewTask(0);
            definition.RegistrationInfo.Description =
                "EQRisk daily update: catches up every pending session and publishes it if the quality gates pass.";
            definition.Settings.StartWhenAvailable = true;
            definition.Settings.ExecutionTimeLimit = "PT3H";
            definition.Settings.DisallowStartIfOnBatteries = false;
            definition.Settings.StopIfGoingOnBatteries = false;
            definition.Settings.MultipleInstances = InstancesIgnoreNew;
            dynamic trigger = definition.Triggers.Create(TriggerDaily);
            trigger.StartBoundary = DateTime.Today.Add(at.ToTimeSpan())
                .ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture);
            trigger.DaysInterval = 1;
            dynamic action = definition.Actions.Create(ActionExec);
            action.Path = exePath;
            action.Arguments = arguments;
            action.WorkingDirectory = workingDirectory;
            dynamic folder = service.GetFolder("\\");
            folder.RegisterTaskDefinition(TaskName, definition, CreateOrUpdate, null, null, LogonInteractiveToken, null);
        });
        LogRegistered(TaskName, at);
    }

    private void WithTask(Action<dynamic> act) => WithService(service =>
    {
        dynamic folder = service.GetFolder("\\");
        act(folder.GetTask(TaskName));
    });

    private static void WithService(Action<dynamic> act)
    {
        var type = Type.GetTypeFromProgID("Schedule.Service")
                   ?? throw new PlatformNotSupportedException("Task Scheduler is not available.");
        dynamic service = Activator.CreateInstance(type)!;
        try
        {
            service.Connect();
            act(service);
        }
        finally
        {
            Marshal.FinalReleaseComObject(service);
        }
    }

    private static DateTimeOffset? Valid(DateTime t) => t > NeverRun ? new DateTimeOffset(t) : null;

    // The COM binder turns HRESULT 0x80070002 into FileNotFoundException; a raw COMException can carry it too.
    private static bool IsNotFound(Exception ex) => ex is FileNotFoundException || (ex is COMException c && c.HResult == NotFound);

    [LoggerMessage(Level = LogLevel.Information, Message = "Scheduled task {Name} registered for {At} daily")]
    private partial void LogRegistered(string name, TimeOnly at);

    [LoggerMessage(Level = LogLevel.Information, Message = "Scheduled task {Name} removed")]
    private partial void LogRemoved(string name);
}
