using System.Text;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Feed;
using EQRisk.Core.Jobs;
using EQRisk.Core.Settings;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation.Activity;

/// <summary>
/// The activity bar along the bottom of the window: the job that is running (or just ended), its
/// elapsed time, the six steps of a daily update, and its live log. It is the one place that hears
/// the job runner, and it tells every page when a job ends.
/// </summary>
public sealed partial class JobActivityViewModel : ObservableObject, IDisposable
{
    private const int TailLines = 400;
    private const int ProgressTextLimit = 512 * 1024;

    private readonly IJobRunner runner;
    private readonly IUiDispatcher ui;
    private readonly IMessenger messenger;
    private readonly IEngineFeed feed;
    private readonly INotifier notifier;
    private readonly ISettingsStore settings;
    private readonly TimeProvider clock;
    private readonly Queue<string> tail = new();
    private readonly StringBuilder progressText = new();
    private ITimer? ticker;

    public JobActivityViewModel(IJobRunner runner, IUiDispatcher ui, IMessenger messenger, IEngineFeed feed,
        INotifier notifier, ISettingsStore settings, TimeProvider clock)
    {
        this.runner = runner;
        this.ui = ui;
        this.messenger = messenger;
        this.feed = feed;
        this.notifier = notifier;
        this.settings = settings;
        this.clock = clock;
        runner.JobChanged += OnJobChanged;
        runner.Output += OnOutput;
        if (runner.Current is { } current)
        {
            Show(current);
        }
    }

    public IReadOnlyList<string> StepNames { get; } = DailyProgress.Steps.Select(s => s.Name).ToList();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsVisible))]
    public partial JobRecord? Job { get; set; }

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(StopCommand))]
    public partial bool IsRunning { get; set; }

    [ObservableProperty]
    public partial string Elapsed { get; set; } = "";

    /// <summary>Index into <see cref="StepNames"/> of the step a daily update has reached.</summary>
    [ObservableProperty]
    public partial int Step { get; set; }

    /// <summary>Whether this job is a daily update or re-estimate, whose steps the strip shows.</summary>
    [ObservableProperty]
    public partial bool ShowsSteps { get; set; }

    [ObservableProperty]
    public partial string LogTail { get; set; } = "";

    [ObservableProperty]
    public partial bool IsLogOpen { get; set; }

    /// <summary>How the job ended, once it has: "Finished in 5 min 03 s".</summary>
    [ObservableProperty]
    public partial string? Outcome { get; set; }

    [ObservableProperty]
    public partial bool Failed { get; set; }

    public bool IsVisible => Job is not null;

    public void Dispose()
    {
        runner.JobChanged -= OnJobChanged;
        runner.Output -= OnOutput;
        ticker?.Dispose();
    }

    [RelayCommand(CanExecute = nameof(IsRunning))]
    private Task StopAsync() => runner.StopAsync();

    [RelayCommand]
    private void ToggleLog() => IsLogOpen = !IsLogOpen;

    [RelayCommand]
    private void Dismiss()
    {
        if (!IsRunning)
        {
            Job = null;
        }
    }

    private void OnJobChanged(object? sender, JobRecord record) => ui.Post(() => Show(record));

    private void OnOutput(object? sender, JobOutput output) => ui.Post(() => Append(output));

    private void Show(JobRecord record)
    {
        var started = !IsRunning && record.IsRunning;
        Job = record;
        IsRunning = record.IsRunning;
        ShowsSteps = record.Kind is JobKind.DailyUpdate or JobKind.Reestimate;
        if (started)
        {
            tail.Clear();
            progressText.Clear();
            LogTail = "";
            Step = 0;
            Outcome = null;
            Failed = false;
            ticker?.Dispose();
            ticker = clock.CreateTimer(_ => ui.Post(Tick), null, TimeSpan.Zero, TimeSpan.FromSeconds(1));
            messenger.Send(new JobStartedMessage(record));
            return;
        }

        if (record.IsRunning)
        {
            return;
        }

        ticker?.Dispose();
        ticker = null;
        Elapsed = Fmt.Duration(record.Duration);
        Failed = record.Status is not JobStatus.Succeeded;
        if (record.Status == JobStatus.Succeeded && ShowsSteps)
        {
            Step = StepNames.Count - 1;
        }

        Outcome = record.Status switch
        {
            JobStatus.Succeeded => $"Finished in {Elapsed}.",
            JobStatus.Stopped => $"Stopped after {Elapsed}.",
            JobStatus.Interrupted => "Interrupted when the app closed.",
            _ => $"Failed after {Elapsed} (exit code {record.ExitCode}). The log shows why.",
        };
        if (Failed)
        {
            IsLogOpen = true;
        }

        _ = RefreshEngineAsync(record);
        if (settings.Current.NotifyOnFinish)
        {
            notifier.Notify(record.Label, Outcome, Failed ? NoticeKind.Error : NoticeKind.Success);
        }
    }

    private async Task RefreshEngineAsync(JobRecord record)
    {
        try
        {
            await feed.RefreshAsync();              // drop the engine's caches before pages re-read
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            // Pages will show the engine's state when they reload.
        }

        messenger.Send(new JobFinishedMessage(record));
    }

    private void Append(JobOutput output)
    {
        if (Job is not { } job || output.JobId != job.Id)
        {
            return;
        }

        foreach (var line in output.Text.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            tail.Enqueue(line.TrimEnd('\r'));
            while (tail.Count > TailLines)
            {
                tail.Dequeue();
            }
        }

        LogTail = string.Join(Environment.NewLine, tail);
        if (!ShowsSteps)
        {
            return;
        }

        progressText.Append(output.Text);
        if (progressText.Length > ProgressTextLimit)
        {
            progressText.Remove(0, progressText.Length - ProgressTextLimit);
        }

        Step = Math.Max(Step, DailyProgress.CurrentStep(progressText.ToString()));
    }

    private void Tick()
    {
        if (Job is { IsRunning: true } job)
        {
            Elapsed = Fmt.Duration(clock.GetUtcNow() - job.StartedAt);
        }
    }
}
