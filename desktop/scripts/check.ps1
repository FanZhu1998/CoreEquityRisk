<#
.SYNOPSIS
    Build the desktop app and run all of its tests.

.DESCRIPTION
    The pre-commit hook for changes under desktop\ runs this. Warnings fail the build
    (Directory.Build.props), and the tests include the engine protocol against the real Python
    engine, so .venv must exist.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\scripts\check.ps1
#>
$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $PSScriptRoot
$dotnet = (Get-Command dotnet -ErrorAction SilentlyContinue).Source
if (-not $dotnet) { $dotnet = Join-Path $env:ProgramFiles "dotnet\dotnet.exe" }
if (-not (Test-Path $dotnet)) { throw "The .NET 10 SDK is not installed: https://dotnet.microsoft.com/download/dotnet/10.0" }
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"

# global.json (the SDK pin and the Microsoft.Testing.Platform runner) applies from desktop\ only.
Push-Location $desktop
try {
    & $dotnet test --solution EQRisk.slnx
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $code
