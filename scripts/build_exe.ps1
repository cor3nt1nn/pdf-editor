<#
.SYNOPSIS
    Build the portable Windows release of PDF Editor.

.DESCRIPTION
    Syncs the dev environment, checks the translations, runs PyInstaller on
    pdfeditor.spec (onedir, windowed), copies LICENSE, THIRD_PARTY_LICENSES.md (when
    present), README.md and the licence texts (licenses\) next to PDFEditor.exe, zips
    dist\PDFEditor into dist\PDFEditor-<version>-win64.zip and prints the sizes and the
    exe's version.

    -Installer then compiles installer\pdfeditor.iss with Inno Setup (ISCC.exe from
    scripts\fetch_innosetup.ps1: $env:ISCC, an installed Inno Setup 7.1+, or the pinned
    portable 7.1.0 package downloaded into build\tools) into
    dist\PDFEditor-<version>-setup.exe, fails on any compiler warning, prints its size and
    checks its version resource.

    -Smoke then runs the frozen tests (tests/test_frozen.py) against the new build, and
    with -Installer also the installer tests (tests/test_installer.py: a silent per-user
    install into a temporary folder, upgrade and uninstall).

.EXAMPLE
    powershell -File scripts\build_exe.ps1 -Smoke

.EXAMPLE
    powershell -File scripts\build_exe.ps1 -Installer -Smoke
#>
param([switch]$Smoke, [switch]$Installer)

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

# Size cap of the setup program (tests/test_installer.py checks it too).
$MaxSetupMB = 45

$Init = Get-Content (Join-Path $Root "src\pdfeditor\__init__.py") -Raw
if ($Init -notmatch '__version__ = "([^"]+)"') { throw "__version__ not found" }
$Version = $Matches[1]
# The setup's binary file version must be numeric (n.n.n.n): the leading numbers of
# __version__ ("0.2.0rc1" -> "0.2.0"), padded to four parts. AppVersion keeps the full text.
if ($Version -notmatch '^\d+(\.\d+)*') { throw "__version__ $Version does not start with a number" }
$FileParts = @($Matches[0].Split(".") | Select-Object -First 4)
while ($FileParts.Count -lt 4) { $FileParts += "0" }
$FileVersion = $FileParts -join "."

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

# The licence texts also go next to the exe (they are bundled in _internal by the spec).
$Licenses = Join-Path $Root "src\pdfeditor\resources\licenses"
if (Test-Path $Licenses) {
    $LicensesOut = Join-Path $Dist "licenses"
    New-Item -ItemType Directory -Force $LicensesOut | Out-Null
    Copy-Item (Join-Path $Licenses "*.txt") -Destination $LicensesOut -Force
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

$Setup = $null
if ($Installer) {
    $Iscc = @(& (Join-Path $PSScriptRoot "fetch_innosetup.ps1"))[-1]
    $OutDir = Join-Path $Root "dist"
    $Setup = Join-Path $OutDir "PDFEditor-$Version-setup.exe"
    if (Test-Path $Setup) { Remove-Item $Setup -Force }
    Write-Host "==> Inno Setup: $Iscc"
    # Quoted: PowerShell 5.1 splits an unquoted -dName=0.1.0 at the first dot.
    $IsccArgs = @("-q", "-dAppVersion=$Version", "-dFileVersion=$FileVersion",
        "-dSourceDir=$Dist", "-dOutputDir=$OutDir",
        (Join-Path $Root "installer\pdfeditor.iss"))
    $ErrorActionPreference = "Continue"
    $IsccOutput = @(& $Iscc @IsccArgs 2>&1 | ForEach-Object { "$_" })
    $IsccExit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $IsccOutput | ForEach-Object { Write-Host $_ }
    if ($IsccExit -ne 0) { throw "Inno Setup failed (exit code $IsccExit)" }
    if ($IsccOutput | Where-Object { $_ -match "Warning" }) { throw "Inno Setup reported warnings" }
    if (-not (Test-Path $Setup)) { throw "$Setup was not built" }
    $SetupBytes = (Get-Item $Setup).Length
    # Inno Setup pads its version strings with spaces.
    $SetupVersion = "$((Get-Item $Setup).VersionInfo.ProductVersion)".Trim()
    Write-Host ("  setup   {0,10}  {1}" -f (Format-MB $SetupBytes), $Setup)
    $SetupFileVersion = "$((Get-Item $Setup).VersionInfo.FileVersionRaw)"
    Write-Host ("  setup version resource: ProductVersion {0}, FileVersion {1}" -f $SetupVersion, $SetupFileVersion)
    if ($SetupVersion -ne $Version) { throw "setup version resource $SetupVersion != $Version" }
    if ($SetupFileVersion -ne $FileVersion) { throw "setup file version $SetupFileVersion != $FileVersion" }
    if ($SetupBytes -gt $MaxSetupMB * 1MB) { throw "setup is larger than $MaxSetupMB MB" }
}

if ($Smoke) {
    $env:PDFEDITOR_FROZEN_EXE = $Exe
    try {
        Invoke-Step "frozen tests" { uv run pytest -m frozen -v }
    } finally {
        Remove-Item Env:PDFEDITOR_FROZEN_EXE
    }
    if ($Setup) {
        $env:PDFEDITOR_INSTALLER = $Setup
        try {
            Invoke-Step "installer tests" { uv run pytest -m installer -v }
        } finally {
            Remove-Item Env:PDFEDITOR_INSTALLER
        }
    }
}
