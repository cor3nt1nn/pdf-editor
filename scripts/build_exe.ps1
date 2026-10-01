<#
.SYNOPSIS
    Build the portable Windows release of PDF Editor.

.DESCRIPTION
    Syncs the dev environment, checks the translations, runs PyInstaller on
    pdfeditor.spec (onedir, windowed), copies LICENSE, THIRD_PARTY_LICENSES.md (when
    present) and README.md next to PDFEditor.exe, zips dist\PDFEditor into
    dist\PDFEditor-<version>-win64.zip and prints the sizes and the exe's version.

    -Smoke then runs the frozen tests (tests/test_frozen.py) against the new build.

.EXAMPLE
    powershell -File scripts\build_exe.ps1 -Smoke
#>
param([switch]$Smoke)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Step([string]$Title, [scriptblock]$Command) {
    Write-Host "==> $Title"
    # Native tools log to stderr: with redirected output, Windows PowerShell 5.1 would
    # turn that into terminating errors under "Stop". Rely on the exit code instead.
    $ErrorActionPreference = "Continue"
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Title failed (exit code $LASTEXITCODE)" }
}

function Format-MB([long]$Bytes) { "{0:N1} MB" -f ($Bytes / 1MB) }

$Init = Get-Content (Join-Path $Root "src\pdfeditor\__init__.py") -Raw
if ($Init -notmatch '__version__ = "([^"]+)"') { throw "__version__ not found" }
$Version = $Matches[1]

Invoke-Step "uv sync --extra dev" { uv sync --extra dev }
Invoke-Step "check translations" { uv run python scripts/check_i18n.py --fresh }
Invoke-Step "PyInstaller" { uv run pyinstaller --noconfirm --clean pdfeditor.spec }

$Dist = Join-Path $Root "dist\PDFEditor"
$Exe = Join-Path $Dist "PDFEditor.exe"
if (-not (Test-Path $Exe)) { throw "$Exe was not built" }

foreach ($Name in "LICENSE", "THIRD_PARTY_LICENSES.md", "README.md") {
    $Source = Join-Path $Root $Name
    if (Test-Path $Source) {
        Copy-Item $Source -Destination $Dist -Force
    } elseif ($Name -ne "THIRD_PARTY_LICENSES.md") {
        throw "$Name is missing"
    }
}

$Zip = Join-Path $Root "dist\PDFEditor-$Version-win64.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Write-Host "==> zip $Zip"
Compress-Archive -Path $Dist -DestinationPath $Zip -CompressionLevel Optimal

$FolderBytes = (Get-ChildItem $Dist -Recurse -File | Measure-Object -Property Length -Sum).Sum
$Info = (Get-Item $Exe).VersionInfo
Write-Host ""
Write-Host "PDF Editor $Version"
Write-Host ("  folder  {0,10}  {1}" -f (Format-MB $FolderBytes), $Dist)
Write-Host ("  exe     {0,10}  {1}" -f (Format-MB (Get-Item $Exe).Length), $Exe)
Write-Host ("  zip     {0,10}  {1}" -f (Format-MB (Get-Item $Zip).Length), $Zip)
Write-Host ("  version resource: FileVersion {0}, ProductVersion {1}" -f $Info.FileVersion, $Info.ProductVersion)
if ($Info.ProductVersion -ne $Version) { throw "version resource $($Info.ProductVersion) != $Version" }

if ($Smoke) {
    $env:PDFEDITOR_FROZEN_EXE = $Exe
    try {
        Invoke-Step "frozen tests" { uv run pytest -m frozen -v }
    } finally {
        Remove-Item Env:PDFEDITOR_FROZEN_EXE
    }
}
