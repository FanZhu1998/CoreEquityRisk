using EQRisk.Core.Engine;
using Tomlyn;

namespace EQRisk.Infrastructure.Engine;

/// <summary>
/// Finds the engine: a folder whose pyproject.toml names the <c>eqrisk</c> project, with its virtual
/// environment built. pyproject.toml is read with Tomlyn.
/// </summary>
public sealed class EngineLocator : IEngineLocator
{
    private static readonly TomlSerializerOptions Toml = new() { PropertyNameCaseInsensitive = true };

    public EngineCheck Check(string root)
    {
        if (string.IsNullOrWhiteSpace(root) || !Directory.Exists(root))
        {
            return EngineCheck.Fail("The folder does not exist.");
        }

        var full = Path.GetFullPath(root);
        var pyproject = Path.Combine(full, "pyproject.toml");
        if (!File.Exists(pyproject))
        {
            return EngineCheck.Fail("There is no pyproject.toml here, so this is not the CoreEquityRisk folder.");
        }

        PyProject? doc;
        try
        {
            doc = TomlSerializer.Deserialize<PyProject>(File.ReadAllText(pyproject), Toml);
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            return EngineCheck.Fail($"pyproject.toml could not be read: {ex.Message}");
        }

        if (!string.Equals(doc?.Project?.Name, "eqrisk", StringComparison.OrdinalIgnoreCase))
        {
            return EngineCheck.Fail("pyproject.toml here describes another project, not eqrisk.");
        }

        if (!Directory.Exists(Path.Combine(full, "configs")))
        {
            return EngineCheck.Fail("The configs folder is missing.");
        }

        var python = Path.Combine(full, ".venv", "Scripts", "python.exe");
        if (!File.Exists(python))
        {
            return EngineCheck.Fail("The engine's Python environment is not built yet. Run `uv sync` in that folder.");
        }

        return new EngineCheck(new EngineInfo(full, python, doc!.Project!.Version ?? "unknown"), null);
    }

    public EngineCheck Locate(string? configuredRoot, string appDirectory)
    {
        if (!string.IsNullOrWhiteSpace(configuredRoot))
        {
            return Check(configuredRoot);
        }

        for (var dir = new DirectoryInfo(appDirectory); dir is not null; dir = dir.Parent)
        {
            var check = Check(dir.FullName);
            if (check.Ok)
            {
                return check;
            }
        }

        return EngineCheck.Fail("Choose the CoreEquityRisk folder in Settings.");
    }

    /// <summary>The parts of pyproject.toml the locator reads.</summary>
    public sealed class PyProject
    {
        public ProjectTable? Project { get; set; }
    }

    public sealed class ProjectTable
    {
        public string? Name { get; set; }

        public string? Version { get; set; }
    }
}
