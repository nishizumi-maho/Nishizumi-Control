param(
    [switch]$TestsOnly
)

# Builds the portable folder and its ZIP with a Python that lives inside this
# project: nothing is installed or registered on the machine that compiles it.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRoot = Join-Path $Root ".build_runtime"
$DownloadDir = Join-Path $RuntimeRoot "downloads"
$PipCacheDir = Join-Path $RuntimeRoot "pip_cache"
$BuildTempDir = Join-Path $RuntimeRoot "temp"
$WinPythonHome = Join-Path $RuntimeRoot "WPy64-312101"
$Python = Join-Path $WinPythonHome "python\python.exe"
$RuntimeReady = Join-Path $WinPythonHome ".dominant-control-runtime-ready"
$Archive = Join-Path $DownloadDir "Winpython64-3.12.10.1dot.zip"
$WinPythonUrl = "https://github.com/winpython/winpython/releases/download/16.6.20250620final/Winpython64-3.12.10.1dot.zip"
$WinPythonSha256 = "7a1f004aec39615977b2b245423a50115530d16af3418df77977186a555d0a40"
$Requirements = Join-Path $Root "requirements-build.txt"
$DistRoot = Join-Path $Root "dist"
$ProductName = "Dominant Control"
$ExecutableName = "DominantControl.exe"
$PortableDir = Join-Path $DistRoot "DominantControl_Portable"
$OutputZip = Join-Path $DistRoot "DominantControl_Portable_v13_Windows_x64.zip"
$PreservedData = Join-Path $RuntimeRoot "preserved_portable_data"
$SpecFile = Join-Path $Root "build\DominantControl.spec"
$LauncherFile = Join-Path $Root "build\START_DOMINANT_CONTROL.bat"
$ReadmeFile = Join-Path $Root "README_PORTABLE.txt"
$TireDistRoot = Join-Path $Root "build\external_dist"
$TireWorkRoot = Join-Path $Root "build\work_tire"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("==> " + $Text) -ForegroundColor Cyan
}

function Assert-LastExit([string]$Action) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed (exit code $LASTEXITCODE)."
    }
}

function Remove-DirectoryRobust([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    for ($Attempt = 1; $Attempt -le 4; $Attempt++) {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $Path)) {
            return
        }
        Start-Sleep -Milliseconds 250
    }
    throw "Could not clean the folder: $Path. Close whatever is using it and try again."
}

function Expand-WinPythonRuntime([string]$ArchivePath, [string]$Destination) {
    # Expand-Archive on Windows PowerShell 5.1 may abort when antivirus removes
    # one of WinPython's optional launcher EXEs while the ZIP module is still
    # processing it.  The compiler only needs the self-contained python folder,
    # so extract it entry by entry and skip IDLE/Jupyter/Spyder launchers.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $RootFull = [IO.Path]::GetFullPath($Destination)
    $RootPrefix = $RootFull.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $ArchiveHandle = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    $EntryPrefix = "WPy64-312101/python/"
    $ExtractedFiles = 0
    try {
        foreach ($Entry in $ArchiveHandle.Entries) {
            $EntryName = $Entry.FullName.Replace("\", "/")
            if (-not $EntryName.StartsWith($EntryPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                continue
            }
            if (
                $EntryName.Contains("/__pycache__/") -or
                $EntryName.EndsWith(".pyc", [StringComparison]::OrdinalIgnoreCase) -or
                $EntryName.StartsWith($EntryPrefix + "Doc/", [StringComparison]::OrdinalIgnoreCase) -or
                $EntryName.StartsWith($EntryPrefix + "Lib/test/", [StringComparison]::OrdinalIgnoreCase) -or
                $EntryName.StartsWith($EntryPrefix + "Lib/idlelib/", [StringComparison]::OrdinalIgnoreCase) -or
                $EntryName.StartsWith($EntryPrefix + "Lib/site-packages/pkg_resources/tests/", [StringComparison]::OrdinalIgnoreCase)
            ) {
                continue
            }
            $TargetPath = [IO.Path]::GetFullPath((Join-Path $Destination $EntryName))
            if (-not $TargetPath.StartsWith($RootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsafe entry found in the ZIP: $EntryName"
            }
            if ([string]::IsNullOrEmpty($Entry.Name)) {
                [IO.Directory]::CreateDirectory($TargetPath) | Out-Null
                continue
            }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($TargetPath)) | Out-Null
            $InputStream = $Entry.Open()
            try {
                $OutputStream = [IO.File]::Open(
                    $TargetPath,
                    [IO.FileMode]::Create,
                    [IO.FileAccess]::Write,
                    [IO.FileShare]::None
                )
                try {
                    $InputStream.CopyTo($OutputStream)
                }
                finally {
                    $OutputStream.Dispose()
                }
            }
            finally {
                $InputStream.Dispose()
            }
            $ExtractedFiles++
        }
    }
    finally {
        $ArchiveHandle.Dispose()
    }
    if ($ExtractedFiles -lt 1000) {
        throw "The ZIP did not provide the expected Python runtime ($ExtractedFiles files)."
    }
    Write-Host "$ExtractedFiles essential Python files extracted."
}

New-Item -ItemType Directory -Path $DownloadDir -Force | Out-Null
New-Item -ItemType Directory -Path $PipCacheDir -Force | Out-Null
New-Item -ItemType Directory -Path $BuildTempDir -Force | Out-Null
$env:PIP_CACHE_DIR = $PipCacheDir
$env:TEMP = $BuildTempDir
$env:TMP = $BuildTempDir

Write-Step "Preparing the local portable Python"
$NeedDownload = -not (Test-Path -LiteralPath $Archive)
if (-not $NeedDownload) {
    $CurrentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
    if ($CurrentHash -ne $WinPythonSha256) {
        Remove-Item -LiteralPath $Archive -Force
        $NeedDownload = $true
    }
}
if ($NeedDownload) {
    Write-Host "Downloading WinPython 3.12.10 (about 39 MB)..."
    Invoke-WebRequest -UseBasicParsing -Uri $WinPythonUrl -OutFile $Archive
}
$CurrentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
if ($CurrentHash -ne $WinPythonSha256) {
    throw "The WinPython SHA-256 does not match. Download refused."
}

if (-not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $RuntimeReady)) {
    if (Test-Path -LiteralPath $WinPythonHome) {
        Remove-DirectoryRobust $WinPythonHome
    }
    Write-Host "Extracting only the Python runtime into .build_runtime..."
    # Extract directly to the final parent.  A GUID staging folder made deep
    # pip paths exceed MAX_PATH on Windows PowerShell 5.1.  The ready marker
    # below makes an interrupted direct extraction safe to retry.
    Expand-WinPythonRuntime $Archive $RuntimeRoot
    if (-not (Test-Path -LiteralPath $Python)) {
        throw "Unexpected structure inside the WinPython package."
    }
}

& $Python -c "import sys, tkinter; print('Python', sys.version.split()[0], '| Tk', tkinter.TkVersion)"
Assert-LastExit "Portable Python validation"
Set-Content -LiteralPath $RuntimeReady -Value "Dominant Control build runtime ready" -Encoding ASCII

Write-Step "Installing the dependencies into the local Python only"
& $Python -m pip install --disable-pip-version-check --no-warn-script-location -r $Requirements
Assert-LastExit "Local dependency install"

& $Python -c "import PyInstaller, irsdk, keyboard, numpy, psutil, tkinter, win32gui; from PyQt5 import QtWidgets as Qt5Widgets; from PySide6 import QtWidgets as Qt6Widgets; print('Dependencies validated')"
Assert-LastExit "Dependency validation"

Write-Step "Checking the code"
& $Python -m compileall -q (Join-Path $Root "dominant_control")
Assert-LastExit "Syntax check"
& $Python (Join-Path $Root "tests\run_tests.py")
Assert-LastExit "Automated tests"

if ($TestsOnly) {
    Write-Host ""
    Write-Host "All tests passed. No executable was produced." -ForegroundColor Green
    exit 0
}

Write-Step "Producing $ExecutableName in onedir mode"
if (Test-Path -LiteralPath (Join-Path $PortableDir "data")) {
    if (Test-Path -LiteralPath $PreservedData) {
        throw "A pending backup already exists at $PreservedData. Preserve it before continuing."
    }
    Move-Item -LiteralPath (Join-Path $PortableDir "data") -Destination $PreservedData
    Write-Host "Data from the previous run preserved temporarily."
}
Remove-DirectoryRobust $PortableDir
foreach ($Leftover in @($OutputZip, ($OutputZip + ".sha256"))) {
    if (Test-Path -LiteralPath $Leftover) {
        Remove-Item -LiteralPath $Leftover -Force
    }
}
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean `
        --distpath $DistRoot `
        --workpath (Join-Path $Root "build\work") `
        $SpecFile
    Assert-LastExit "PyInstaller"
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath (Join-Path $PortableDir $ExecutableName))) {
    throw "$ExecutableName was not found after the build."
}

Write-Step "Producing the original Tire Wear overlay as an isolated Qt process"
Remove-DirectoryRobust $TireDistRoot
Remove-DirectoryRobust $TireWorkRoot
Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --clean `
        --distpath $TireDistRoot `
        --workpath $TireWorkRoot `
        (Join-Path $Root "build\TireOverlayOriginal.spec")
    Assert-LastExit "PyInstaller for the original Tire Wear"
}
finally {
    Pop-Location
}

$TireSource = Join-Path $TireDistRoot "NishizumiTireOriginal"
$TireExecutable = Join-Path $TireSource "NishizumiTireOriginal.exe"
if (-not (Test-Path -LiteralPath $TireExecutable)) {
    throw "NishizumiTireOriginal.exe was not found after the build."
}
$ExternalOverlayRoot = Join-Path $PortableDir "external_overlays"
New-Item -ItemType Directory -Path $ExternalOverlayRoot -Force | Out-Null
Copy-Item -LiteralPath $TireSource -Destination $ExternalOverlayRoot -Recurse -Force

Set-Content -LiteralPath (Join-Path $PortableDir "portable.mode") -Value "$ProductName portable data mode" -Encoding ASCII
Copy-Item -LiteralPath $LauncherFile -Destination $PortableDir -Force
Copy-Item -LiteralPath $ReadmeFile -Destination $PortableDir -Force
Copy-Item -LiteralPath (Join-Path $Root "licenses") -Destination (Join-Path $PortableDir "licenses") -Recurse -Force

Write-Step "Creating the clean portable ZIP"
if (Test-Path -LiteralPath $OutputZip) {
    Remove-Item -LiteralPath $OutputZip -Force
}
Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $OutputZip -CompressionLevel Optimal
$ZipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputZip).Hash.ToLowerInvariant()
Set-Content -LiteralPath ($OutputZip + ".sha256") -Value ("$ZipHash  " + [IO.Path]::GetFileName($OutputZip)) -Encoding ASCII

if (Test-Path -LiteralPath $PreservedData) {
    Move-Item -LiteralPath $PreservedData -Destination (Join-Path $PortableDir "data")
    Write-Host "Data from the previous run restored inside the built folder."
}

Write-Host ""
Write-Host "SUCCESS" -ForegroundColor Green
Write-Host "Application: $PortableDir"
Write-Host "File to distribute: $OutputZip"
Write-Host "SHA-256: $ZipHash"
Write-Host "Nothing was installed or registered on Windows."
