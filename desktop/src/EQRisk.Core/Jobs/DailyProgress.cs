namespace EQRisk.Core.Jobs;

/// <summary>One step of <c>eqrisk run-daily</c>, recognised by words the engine logs when it gets there.</summary>
public sealed record DailyStep(string Name, IReadOnlyList<string> Markers);

/// <summary>
/// Where a daily update has got to, read from its log. The markers are event names the engine
/// logs at each stage (eqrisk/pipeline/daily.py and the stages it calls);
/// tests/pipeline/test_desktop_contract.py fails if one of them disappears from the engine.
/// </summary>
public static class DailyProgress
{
    public static IReadOnlyList<DailyStep> Steps { get; } =
    [
        new("Wait for data", ["waiting for vendor"]),
        new("Ingest", ["ingested"]),
        new("Stage", ["staging", "security master", "fundamentals staged"]),
        new("Exposures", ["descriptors"]),
        new("Factor model", ["exposures"]),      // regression, covariance and specific risk follow this line
        new("Gates", ["notify"]),
    ];

    /// <summary>Index of the latest step whose marker appears in the log so far (0 before any).</summary>
    public static int CurrentStep(string logText)
    {
        ArgumentNullException.ThrowIfNull(logText);
        var reached = 0;
        for (var i = 0; i < Steps.Count; i++)
        {
            foreach (var marker in Steps[i].Markers)
            {
                if (logText.Contains(marker, StringComparison.Ordinal))
                {
                    reached = i;
                    break;
                }
            }
        }

        return reached;
    }
}
