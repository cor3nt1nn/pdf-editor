<#
.SYNOPSIS
    Find the Inno Setup compiler (ISCC.exe), downloading a pinned portable copy if needed.

.DESCRIPTION
    Writes the full path of ISCC.exe to the output, looking in this order:
      1. $env:ISCC (a path to ISCC.exe);
      2. an installed Inno Setup 7, then 6 (Program Files, Program Files (x86) and the
         per-user %LOCALAPPDATA%\Programs);
      3. build\tools\innosetup\tools\ISCC.exe (an earlier download);
      4. otherwise downloads the portable Tools.InnoSetup 7.1.0 NuGet package from
         nuget.org, checks its SHA-256 and unpacks it into build\tools\innosetup\
         (build\ is not committed). Nothing is installed system-wide, no admin rights.

.EXAMPLE
    $Iscc = & scripts\fetch_innosetup.ps1
#>
param()

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

$Version = "7.1.0"
$Url = "https://www.nuget.org/api/v2/package/Tools.InnoSetup/$Version"
$Sha256 = "aad15c662593c6f5656457a5c001f4591babd72820cacf63f9706d4af5d93b75"

if ($env:ISCC) {
    if (-not (Test-Path $env:ISCC -PathType Leaf)) { throw "ISCC=$env:ISCC does not exist" }
    Write-Output (Resolve-Path $env:ISCC).Path
    return
}

$Bases = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA "Programs"))
foreach ($Major in 7, 6) {
    foreach ($Base in $Bases) {
        if (-not $Base) { continue }
        $Candidate = Join-Path $Base "Inno Setup $Major\ISCC.exe"
        if (Test-Path $Candidate -PathType Leaf) {
            Write-Output $Candidate
            return
        }
    }
}

$Tools = Join-Path $Root "build\tools"
$Target = Join-Path $Tools "innosetup"
$Iscc = Join-Path $Target "tools\ISCC.exe"
if (Test-Path $Iscc -PathType Leaf) {
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
