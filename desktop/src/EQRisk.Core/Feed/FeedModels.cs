using System.Text.Json;
using System.Text.Json.Serialization;

namespace EQRisk.Core.Feed;

// The records below mirror the JSON that `eqrisk serve` returns (eqrisk/pipeline/feed.py).
// FeedJson.Options maps PascalCase to snake_case; names that do not follow that pattern say so.
// Numbers that can be missing in the engine (NaN, or no row) are nullable, because the engine
// sends them as null rather than inventing a value.

public sealed record EngineHello(
    int Protocol, string EngineVersion, string ModelId, string Root, string Python, int Pid,
    IReadOnlyList<string> Methods);

/// <summary>Whether .env sets a key. The engine never sends a value.</summary>
public sealed record KeyPresence(string Env, bool Present);

public sealed record GateResult(string Gate, string Level, bool Ok, string Detail);

/// <summary>One run manifest: what ran, how it ended, which gates failed.</summary>
public sealed record RunInfo(
    string RunId, string Command, DateOnly? AsOf, string Status,
    DateTimeOffset StartedAt, DateTimeOffset? FinishedAt, double? Minutes,
    IReadOnlyList<GateResult> Gates, IReadOnlyList<string> FailGates, IReadOnlyList<string> WarnGates,
    string ConfigHash, string GitSha, bool GitDirty);

/// <summary>Where the model stands: the last processed session, what is pending, the last daily run.</summary>
public sealed record ModelStatus(
    string ModelId, string EngineVersion, string NowEt, DateOnly? Last, DateOnly? Target,
    IReadOnlyList<DateOnly> Pending, DateOnly? NextSession, string ReadyAfterEt, DateOnly? LatestGood,
    int DailyRuns, RunInfo? LastRun, IReadOnlyList<KeyPresence> Keys)
{
    public bool UpToDate => Pending.Count == 0;

    public IReadOnlySet<string> KeysInDotEnv => Keys.Where(k => k.Present).Select(k => k.Env).ToHashSet();
}

public sealed record ModelDates(IReadOnlyList<DateOnly> Dates, DateOnly? Latest);

public sealed record FactorMove(string Factor, string Group, double? F, double? TStat, double? FOverSigma);

/// <summary>One session: the market (country factor) return, the fit, the regime multipliers and every factor.</summary>
public sealed record DayInfo(
    DateOnly Date, double? Country, double? R2, int? N, double? Cond, string? FitStatus,
    [property: JsonPropertyName("lambda_F")] double? LambdaF,
    [property: JsonPropertyName("lambda_S")] double? LambdaS,
    IReadOnlyList<FactorMove> Moves);

public sealed record HistoryInfo(
    DateOnly AsOf, int Years, IReadOnlyList<DateOnly> Dates,
    IReadOnlyDictionary<string, IReadOnlyList<double?>> Cum,
    IReadOnlyList<DateOnly> VolDates,
    IReadOnlyDictionary<string, IReadOnlyList<double?>> VolAnn,
    IReadOnlyList<DateOnly> VraDates,
    [property: JsonPropertyName("lambda_F")] IReadOnlyList<double?> LambdaF,
    [property: JsonPropertyName("lambda_S")] IReadOnlyList<double?> LambdaS,
    IReadOnlyList<double?> CsVol,
    [property: JsonPropertyName("r2_dates")] IReadOnlyList<DateOnly> R2Dates,
    [property: JsonPropertyName("r2")] IReadOnlyList<double?> R2);

public sealed record FactorVol(string Factor, string Group, double? VolAnn, double? VolPreEigenAnn);

public sealed record FactorRiskInfo(
    DateOnly AsOf, IReadOnlyList<FactorVol> Factors, IReadOnlyList<IReadOnlyList<double?>> Corr);

/// <summary>A name's exposures. Style columns arrive by name, so they are kept as extension data.</summary>
public sealed class ExposureRow
{
    public long Sid { get; init; }

    public string? Ticker { get; init; }

    public string? Industry { get; init; }

    public bool? InEstu { get; init; }

    [JsonExtensionData]
    public Dictionary<string, JsonElement> Styles { get; init; } = [];

    public double? Style(string name) =>
        Styles.TryGetValue(name, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetDouble() : null;
}

public sealed record ExposuresInfo(DateOnly AsOf, IReadOnlyList<string> Styles, IReadOnlyList<ExposureRow> Rows);

public sealed record SpecificRow(
    long Sid, string? Ticker, string? Industry, bool? InEstu,
    double? SigmaTs, double? SigmaStr, double? SigmaBlend, double? SigmaFinal, double? Gamma);

public sealed record SpecificInfo(DateOnly AsOf, IReadOnlyList<SpecificRow> Rows);

/// <summary>One line of the section 1.3 scorecard or the external checks.</summary>
public sealed record ScoreRow(string Area, string Criterion, string Value, string Status);

public sealed record BiasRow(
    string Portfolio, double? N, double? Bias, double? Band, bool? Inside, double? Mrad, double? Qlike);

public sealed record EigenBiasRow(double K, double? N, double? BiasBefore, double? BiasAfter);

public sealed record DecileBiasRow(
    string Grouping, double Decile,
    [property: JsonPropertyName("full stack")] double? FullStack,
    [property: JsonPropertyName("time series only")] double? TimeSeriesOnly);

public sealed record ValidationReport(
    DateOnly Start, DateOnly End, int Periods,
    IReadOnlyList<ScoreRow> Scorecard, IReadOnlyList<ScoreRow> External,
    IReadOnlyList<BiasRow> Factor, IReadOnlyList<EigenBiasRow> Eigen,
    IReadOnlyList<DecileBiasRow> SpecificDeciles, IReadOnlyList<BiasRow> Market,
    string Dir, IReadOnlyList<string> Figures);

public sealed record ReferenceSnapshot(string Dataset, string Source, DateOnly? Snapshot);

public sealed record Inventory(
    DateOnly? EodFirst, DateOnly? EodLast, int EodSessions, int EdgarCompanies,
    IReadOnlyList<ReferenceSnapshot> Refs, DateOnly? StagedLast,
    IReadOnlyDictionary<string, JsonElement> Watermarks, string DataDir, long DataBytes);

public sealed record ExceptionCount(string? Area, string Issue, int Count);

public sealed record ExceptionRow(
    string? Ticker, DateOnly? Start, DateOnly? End, string Issue, string? Detail, string? Area);

public sealed record ExceptionsInfo(int Total, IReadOnlyList<ExceptionCount> Counts, IReadOnlyList<ExceptionRow> Rows);

public sealed record RunsInfo(int Total, IReadOnlyList<RunInfo> Runs);

public sealed record FactorCovSettings(
    double VolHalfLife, double VolNwLags, double CorrHalfLife, double CorrNwLags, double EigenA, double VraHalfLife);

public sealed record SpecificSettings(double HalfLife, double ShrinkageQ);

public sealed record SourceSettings(string Prices, string Fundamentals, string Membership, string RiskFree);

public sealed record EnginePaths(string Root, string Config, string Data, string Reports, string Logs);

public sealed record EngineConfig(
    string ModelId, string Preset, int HorizonDays, string ConfigHash,
    FactorCovSettings FactorCov, SpecificSettings SpecificRisk, SourceSettings Sources, EnginePaths Paths,
    string VendorReadyAfterEt);

public sealed record SiteInfo(string Path, bool Exported, long Bytes, DateTimeOffset? Modified);

public sealed record SnapshotFile(string Name, string Path, long Bytes, DateTimeOffset? Modified);

/// <summary>Where published outputs live: the static viewer and the snapshot files.</summary>
public sealed record OutputsInfo(SiteInfo Site, string ExportsDir, IReadOnlyList<SnapshotFile> Snapshots);

/// <summary>A holding sent to the engine. <c>BenchWeight</c> makes the analysis active.</summary>
public sealed record Holding(string Ticker, double Weight, double? BenchWeight = null);

public sealed record GroupShare(string Group, double? PctVar);

public sealed record FactorContribution(
    string Factor, string Group, double? Exposure, double? VolAnn, double? Corr, double? XsrAnn, double? PctVar);

public sealed record AssetContribution(string? Ticker, double Weight, double? MctrAnn, double? PctRisk, double? Beta);

public sealed record PortfolioReport(
    DateOnly AsOf, bool Active, double SigmaAnn, double? FactorShare, double? SpecificShare, double? Beta,
    IReadOnlyList<GroupShare> Groups, IReadOnlyList<FactorContribution> Factors,
    IReadOnlyList<AssetContribution> Assets, IReadOnlyList<string> Unmatched, int Matched);

/// <summary>What to optimize (eqrisk/optimize/service.py). Bands and caps are fractions: 0.03 is 3%.</summary>
public sealed record OptimizeRequest(
    string Method, double TeMaxAnn, double StyleBand, double IndBand, double WMax, double TurnoverMax,
    string? AlphaFactor = null, double AlphaPerUnit = 0.0);

public sealed record OptimizedHolding(string? Ticker, double Weight, double Benchmark);

/// <summary>The optimizer's answer. When <c>Ok</c> is false only <c>Status</c> is set.</summary>
public sealed record OptimizeOutcome(
    DateOnly AsOf, bool Ok, string Status,
    double? SigmaAnn = null, double? FactorShare = null, double? SpecificShare = null, double? Beta = null,
    IReadOnlyList<GroupShare>? Groups = null, IReadOnlyList<FactorContribution>? Factors = null,
    IReadOnlyList<AssetContribution>? Assets = null, int? NamesHeld = null, double? Turnover = null,
    IReadOnlyList<OptimizedHolding>? Holdings = null);
