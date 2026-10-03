<#
.SYNOPSIS
    Find the Inno Setup compiler (ISCC.exe), downloading a pinned portable copy if needed.

.DESCRIPTION
    Writes the full path of ISCC.exe to the output, looking in this order:
      1. $env:ISCC (a path to ISCC.exe; taken as is, whatever its version);
      2. an installed Inno Setup (Program Files, Program Files (x86) and the per-user
         %LOCALAPPDATA%\Programs) whose ISCC.exe file version is at least 7.1 — an older
         compiler (6.x, 7.0) is skipped with a note;
      3. build\tools\innosetup\tools\ISCC.exe (an earlier download, same check);
      4. otherwise downloads the portable Tools.InnoSetup 7.1.0 NuGet package from
         nuget.org, checks its SHA-256 and unpacks it into build\tools\innosetup\
         (build\ is not committed). Nothing is installed system-wide, no admin rights.

.PARAMETER SearchRoots
    Folders searched for "Inno Setup 7\ISCC.exe" and "Inno Setup 6\ISCC.exe" instead of
    Program Files, Program Files (x86) and %LOCALAPPDATA%\Programs (tests).

.EXAMPLE
    $Iscc = & scripts\fetch_innosetup.ps1
#>
param([string[]]$SearchRoots)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$Version = "7.1.0"
# The script needs Inno Setup 7.1 (SetupArchitecture, the modern wizard used here).
$MinVersion = [version]"7.1"

# The version of the compiler at $Path: its file version, or — the portable package's
# binaries carry none (0.0.0.0) — the "Compiler engine version" it prints for a script.
function Get-IsccVersion([string]$Path) {
    $Raw = (Get-Item $Path).VersionInfo.FileVersionRaw
    if ($Raw -and $Raw.Major -gt 0) { return $Raw }
    $Probe = Join-Path ([IO.Path]::GetTempPath()) "pdfeditor-iscc-version.iss"
    Set-Content -Path $Probe -Value "; version probe (no [Setup]: nothing is built)" -Encoding ASCII
    $ErrorActionPreference = "Continue"
    $Out = @(& $Path $Probe 2>&1 | ForEach-Object { "$_" })
    Remove-Item $Probe -Force -ErrorAction SilentlyContinue
    foreach ($Line in $Out) {
        if ($Line -match 'Compiler engine version: Inno Setup (\d+(\.\d+)+)') { return [version]$Matches[1] }
    }
    return [version]"0.0"
}

# ISCC.exe at $Path is recent enough (its version, not its folder's name).
function Test-Iscc([string]$Path) {
    if (-not (Test-Path $Path -PathType Leaf)) { return $false }
    $Found = Get-IsccVersion $Path
    if ($Found -ge $MinVersion) { return $true }
    Write-Host "==> skipping $Path (version $Found, needs $MinVersion or later)"
    return $false
}
$Url = "https://www.nuget.org/api/v2/package/Tools.InnoSetup/$Version"
$Sha256 = "aad15c662593c6f5656457a5c001f4591babd72820cacf63f9706d4af5d93b75"

if ($env:ISCC) {
    if (-not (Test-Path $env:ISCC -PathType Leaf)) { throw "ISCC=$env:ISCC does not exist" }
    Write-Output (Resolve-Path $env:ISCC).Path
    return
}

$Bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA "Programs"))
if ($SearchRoots) { $Bases = $SearchRoots }
foreach ($Major in 7, 6) {
    foreach ($Base in $Bases) {
        if (-not $Base) { continue }
        $Candidate = Join-Path $Base "Inno Setup $Major\ISCC.exe"
        if (Test-Iscc $Candidate) {
            Write-Output $Candidate
            return
        }
    }
}

$Tools = Join-Path $Root "build\tools"
$Target = Join-Path $Tools "innosetup"
$Iscc = Join-Path $Target "tools\ISCC.exe"
if (Test-Iscc $Iscc) {
    Write-Output $Iscc
    return
}

New-Item -ItemType Directory -Force $Tools | Out-Null
$Package = Join-Path $Tools "tools.innosetup.$Version.nupkg"
if (-not (Test-Path $Package)) {
    Write-Host "==> download Inno Setup $Version (portable NuGet package) from $Url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $Partial = "$Package.part"
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -Uri $Url -OutFile $Partial -UseBasicParsing
    Move-Item $Partial $Package -Force
}
$Actual = (Get-FileHash $Package -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Actual -ne $Sha256) {
    Remove-Item $Package -Force
    throw "SHA-256 mismatch for $Package`: $Actual, expected $Sha256 (file deleted)"
}

# Expand-Archive only accepts .zip files: unpack a .zip copy of the package.
$Zip = Join-Path $Tools "tools.innosetup.$Version.zip"
Copy-Item $Package $Zip -Force
if (Test-Path $Target) { Remove-Item $Target -Recurse -Force }
Expand-Archive -Path $Zip -DestinationPath $Target
Remove-Item $Zip -Force
if (-not (Test-Path $Iscc -PathType Leaf)) { throw "ISCC.exe not found in $Package" }
Write-Output $Iscc
