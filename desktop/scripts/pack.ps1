<#
.SYNOPSIS
    Build EQRisk for Windows and package it with Velopack.

.DESCRIPTION
    Publishes the WPF app (framework-dependent, win-x64) and runs `vpk pack`. Everything lands in
    desktop\artifacts\releases, which git ignores:

        EQRiskDesktop-win-Setup.exe      one-click installer: Start menu and desktop shortcuts, uninstall entry, updates
        EQRiskDesktop-win-Portable.zip   unzip anywhere and run EQRisk.exe
        *.nupkg, releases.win.json       what a GitHub release needs for in-app updates

    If the PC lacks the .NET 10 Desktop Runtime, Setup.exe installs it first. Nothing is code-signed,
    so SmartScreen asks once (More info, Run anyway).
    The installed app finds the engine by the folder chosen in Settings; a copy run from inside the
    CoreEquityRisk checkout finds it on its own.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File desktop\scripts\pack.ps1
    powershell -ExecutionPolicy Bypass -File desktop\scripts\pack.ps1 -Version 0.2.1
#>
param(
    [string]$Version,
    [string]$Runtime = "win-x64"
)

$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $PSScriptRoot
$dotnet = (Get-Command dotnet -ErrorAction SilentlyContinue).Source
if (-not $dotnet) { $dotnet = Join-Path $env:ProgramFiles "dotnet\dotnet.exe" }
if (-not $Version) {
    $Version = ([xml](Get-Content (Join-Path $desktop "Directory.Build.props"))).Project.PropertyGroup.Version
}

$publish = Join-Path $desktop "artifacts\publish"
$releases = Join-Path $desktop "artifacts\releases"
if (Test-Path $publish) { Remove-Item $publish -Recurse -Force }

Push-Location $desktop
try {
    Write-Host "Publishing EQRisk $Version ($Runtime)..."
    & $dotnet publish src\EQRisk.Desktop\EQRisk.Desktop.csproj -c Release -r $Runtime --self-contained false `
        -p:Version=$Version -o $publish
    if ($LASTEXITCODE) { throw "dotnet publish failed" }

    & $dotnet tool restore
    if ($LASTEXITCODE) { throw "dotnet tool restore failed" }

    Write-Host "Packaging with Velopack..."
    & $dotnet vpk pack --packId EQRiskDesktop --packVersion $Version --packDir $publish --mainExe EQRisk.exe `
        --packTitle EQRisk --packAuthors "Fan Zhu" --icon src\EQRisk.Desktop\Assets\eqrisk.ico `
        --framework net10.0-x64-desktop --outputDir $releases
    if ($LASTEXITCODE) { throw "vpk pack failed" }
}
finally {
    Pop-Location
}

Get-ChildItem $releases -File | Sort-Object Name |
    Format-Table Name, @{ Name = "MB"; Expression = { [math]::Round($_.Length / 1MB, 1) } } -AutoSize
