using EQRisk.Core.Engine;
using EQRisk.Core.Jobs;

namespace EQRisk.Presentation;

// Messages between view models (CommunityToolkit.Mvvm messenger), so no page holds a reference to
// another: the activity bar announces jobs, pages refresh themselves, anything can ask to navigate.

/// <summary>A job started.</summary>
public sealed record JobStartedMessage(JobRecord Job);

/// <summary>A job ended. Pages whose data it may have changed reload.</summary>
public sealed record JobFinishedMessage(JobRecord Job);

/// <summary>Go to a page by its key (for example "today" or "data").</summary>
public sealed record NavigateMessage(string PageKey);

public sealed record EngineStateMessage(EngineConnectionState State, string? Error);

/// <summary>Anything that changes which keys are configured: a key stored or removed, .env edited.</summary>
public sealed record KeysChangedMessage;
