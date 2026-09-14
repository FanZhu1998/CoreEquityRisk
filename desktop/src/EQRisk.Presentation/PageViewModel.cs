using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;

namespace EQRisk.Presentation;

/// <summary>
/// A screen: what it is called, how its heading reads, and how it loads. A page loads when first
/// shown and again after any job ends, so what it shows is never older than the last run.
/// </summary>
public abstract partial class PageViewModel : ObservableObject, IRecipient<JobFinishedMessage>,
    IRecipient<JobStartedMessage>
{
    private bool stale = true;
    private CancellationTokenSource? loading;

    protected PageViewModel(IMessenger messenger)
    {
        ArgumentNullException.ThrowIfNull(messenger);
        Messenger = messenger;
        messenger.RegisterAll(this);
    }

    /// <summary>Stable identifier used by navigation.</summary>
    public abstract string Key { get; }

    /// <summary>The name in the navigation rail.</summary>
    public abstract string Title { get; }

    /// <summary>The small upper-case line above the heading, such as "01 — TODAY".</summary>
    public abstract string Eyebrow { get; }

    /// <summary>The heading before its emphasized part.</summary>
    public abstract string Heading { get; }

    /// <summary>The emphasized end of the heading, drawn in the accent colour.</summary>
    public abstract string HeadingAccent { get; }

    public virtual string? Lede => null;

    [ObservableProperty]
    public partial bool IsLoading { get; set; }

    [ObservableProperty]
    public partial string? Problem { get; set; }

    public bool IsActive { get; private set; }

    protected IMessenger Messenger { get; }

    public async Task ActivateAsync()
    {
        IsActive = true;
        if (stale)
        {
            await ReloadAsync();
        }
    }

    public void Deactivate() => IsActive = false;

    [RelayCommand]
    public async Task ReloadAsync()
    {
        loading?.Cancel();
        using var cts = new CancellationTokenSource();
        loading = cts;
        IsLoading = true;
        Problem = null;
        try
        {
            await LoadAsync(cts.Token);
            stale = false;
        }
        catch (OperationCanceledException) when (cts.IsCancellationRequested)
        {
            // A newer load replaced this one.
        }
        catch (Exception ex) when (Problems.IsExpected(ex))
        {
            Problem = Problems.Describe(ex);
        }
        finally
        {
            if (ReferenceEquals(loading, cts))
            {
                IsLoading = false;
                loading = null;
            }
        }
    }

    public void Receive(JobFinishedMessage message)
    {
        ArgumentNullException.ThrowIfNull(message);
        OnJobFinished(message.Job);
        OnBusyChanged();
        stale = true;
        if (IsActive)
        {
            _ = ReloadAsync();
        }
    }

    public void Receive(JobStartedMessage message) => OnBusyChanged();

    protected abstract Task LoadAsync(CancellationToken ct);

    /// <summary>A job just ended; pages that asked for its output (Test connections) read it here.</summary>
    protected virtual void OnJobFinished(JobRecord job)
    {
    }

    /// <summary>A job started or ended: refresh which job buttons can be pressed.</summary>
    protected virtual void OnBusyChanged()
    {
    }
}

/// <summary>Which failures a page shows as a message instead of crashing, and how they read.</summary>
public static class Problems
{
    public static bool IsExpected(Exception ex) => ex is EngineException or EngineUnavailableException
        or TimeoutException or InvalidOperationException or IOException or JobBusyException
        or UnauthorizedAccessException or System.Text.Json.JsonException;

    public static string Describe(Exception ex)
    {
        ArgumentNullException.ThrowIfNull(ex);
        return ex switch
        {
            EngineException { ErrorType: "NotConfigured" } e => e.Message,
            EngineException { ErrorType: "LookupError" } e => $"Not in the model: {e.Message}",
            EngineException e => $"The engine could not answer: {e.Message}",
            EngineUnavailableException e => $"The engine is not running. {e.Message}",
            System.Text.Json.JsonException => "The engine answered in a form this version of the app does not read. "
                                              + "Update the app and the engine together.",
            _ => ex.Message,
        };
    }
}
