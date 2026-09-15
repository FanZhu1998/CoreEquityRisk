using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using CommunityToolkit.Mvvm.Messaging;
using EQRisk.Core.Engine;
using EQRisk.Presentation.Activity;
using EQRisk.Presentation.Services;

namespace EQRisk.Presentation;

/// <summary>An entry in the navigation rail: a page and the heading it sits under.</summary>
public sealed record NavEntry(PageViewModel Page, string Group);

/// <summary>
/// The window frame: the navigation rail, the page in view, the engine state and the activity bar.
/// Pages come from the container, so adding one is a registration, not an edit here.
/// </summary>
public sealed partial class ShellViewModel : ObservableObject, IRecipient<NavigateMessage>, IDisposable
{
    private static readonly (string Group, string[] Keys)[] Layout =
    [
        ("", ["today", "data", "estimate", "validate"]),
        ("Explore", ["factor-returns", "factor-risk", "exposures", "specific-risk"]),
        ("Portfolio", ["portfolio", "optimizer"]),
        ("System", ["jobs", "publish", "settings"]),
    ];

    private readonly Dictionary<string, PageViewModel> pages;
    private readonly IEngineConnection connection;
    private readonly IUiDispatcher ui;

    public ShellViewModel(IEnumerable<PageViewModel> pages, JobActivityViewModel activity, IEngineConnection connection,
        IUiDispatcher ui, IMessenger messenger)
    {
        ArgumentNullException.ThrowIfNull(messenger);
        this.pages = pages.ToDictionary(p => p.Key, StringComparer.Ordinal);
        Activity = activity;
        this.connection = connection;
        this.ui = ui;
        Entries = Layout
            .SelectMany(g => g.Keys.Where(this.pages.ContainsKey).Select(k => new NavEntry(this.pages[k], g.Group)))
            .ToList();
        EngineText = "";
        messenger.RegisterAll(this);
        connection.StateChanged += OnEngineState;
        UpdateEngine(connection.State);
    }

    /// <summary>The rail, in order; the view groups it by <see cref="NavEntry.Group"/>.</summary>
    public IReadOnlyList<NavEntry> Entries { get; }

    public JobActivityViewModel Activity { get; }

    [ObservableProperty]
    public partial PageViewModel? Current { get; set; }

    [ObservableProperty]
    public partial NavEntry? SelectedEntry { get; set; }

    [ObservableProperty]
    public partial string EngineText { get; set; }

    [ObservableProperty]
    public partial bool EngineReady { get; set; }

    [ObservableProperty]
    public partial bool EngineProblem { get; set; }

    public PageViewModel Page(string key) => pages[key];

    [RelayCommand]
    public async Task NavigateAsync(string key)
    {
        if (!pages.TryGetValue(key, out var page) || ReferenceEquals(page, Current))
        {
            return;
        }

        Current?.Deactivate();
        Current = page;
        SelectedEntry = Entries.FirstOrDefault(e => ReferenceEquals(e.Page, page));
        await page.ActivateAsync();
    }

    public void Receive(NavigateMessage message)
    {
        ArgumentNullException.ThrowIfNull(message);
        _ = NavigateAsync(message.PageKey);
    }

    public void Dispose() => connection.StateChanged -= OnEngineState;

    partial void OnSelectedEntryChanged(NavEntry? value)
    {
        if (value is not null && !ReferenceEquals(value.Page, Current))
        {
            _ = NavigateAsync(value.Page.Key);
        }
    }

    private void OnEngineState(object? sender, EngineConnectionState state) => ui.Post(() => UpdateEngine(state));

    private void UpdateEngine(EngineConnectionState state)
    {
        EngineReady = state == EngineConnectionState.Ready;
        EngineProblem = state is EngineConnectionState.Failed or EngineConnectionState.NotConfigured;
        EngineText = state switch
        {
            EngineConnectionState.Ready => "Engine ready",
            EngineConnectionState.Starting => "Starting the engine…",
            EngineConnectionState.Restarting => "Restarting the engine…",
            EngineConnectionState.NotConfigured => "Engine folder not set",
            EngineConnectionState.Failed => "Engine stopped",
            _ => "Engine idle",
        };
    }
}
