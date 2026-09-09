param(
    [switch]$InnoOnly
)

# Builds the portable folder first (unless -InnoOnly) and then compiles the
# Inno Setup installer with a compiler that also lives inside this project.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Inno Setup's ISCC cannot read source files through paths longer than 260
# characters.  When the project sits in a deep folder, map it to a short drive
# letter for this build only.
$SourceRoot = $Root
$TempDriveLetter = $null
if ($Root.Length -gt 80) {
    for ($Code = 80; $Code -le 90; $Code++) {
        $TempDriveLetter = [string][char]$Code
        if (-not (Test-Path -LiteralPath ($TempDriveLetter + ":\"))) {
            break
        }
        $TempDriveLetter = $null
    }
    if (-not $TempDriveLetter) {
        throw "No free drive letter to shorten the project path."
    }
    & subst ($TempDriveLetter + ":") $Root | Out-Null
    $SourceRoot = $TempDriveLetter + ":\"
    Write-Host ("Project path too long for ISCC; using the temporary mapping " + $SourceRoot)
}

$PortableBuilder = Join-Path $Root "tools\build_portable.ps1"
$ProductName = "Dominant Control"
$ProductVersion = "v13"
$PortableDir = Join-Path $SourceRoot "dist\DominantControl_Portable"
$PortableExe = Join-Path $PortableDir "DominantControl.exe"
$InstallerScript = Join-Path $SourceRoot "installer\DominantControl_v13.iss"
$InstallerOutput = Join-Path $SourceRoot "dist\installer\DominantControl_v13_Setup.exe"

$RuntimeRoot = Join-Path $SourceRoot ".build_runtime"
$InnoRoot = Join-Path $RuntimeRoot "InnoSetup-6.7.3"
$InnoInstaller = Join-Path $RuntimeRoot "downloads\innosetup-6.7.3.exe"
$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host ("==> " + $Text) -ForegroundColor Cyan
}

function Assert-LastExit([string]$Action) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Action failed (exit code $LASTEXITCODE)."
    }
}

if (-not $InnoOnly) {
    Write-Step "Building the portable folder of $ProductName $ProductVersion"

    & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
        -File $PortableBuilder

    Assert-LastExit "Portable build"
}

if (-not (Test-Path -LiteralPath $PortableExe)) {
    throw (
        "The executable was not found in $PortableDir. " +
        "Run without -InnoOnly to produce the portable folder first."
    )
}

$IsccCandidates = @(
    (Join-Path $InnoRoot "ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    "C:\Program Files\Inno Setup 7\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
)

$Iscc = $IsccCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1

if (-not $Iscc) {
    Write-Step "Preparing a portable Inno Setup inside the project folder"

    New-Item -ItemType Directory -Path (
        Split-Path -Parent $InnoInstaller
    ) -Force | Out-Null

    if (-not (Test-Path -LiteralPath $InnoInstaller)) {
        Write-Host "Downloading the official Inno Setup 6.7.3..."
        Invoke-WebRequest -UseBasicParsing -Uri $InnoUrl -OutFile $InnoInstaller
    }

    $Signature = Get-AuthenticodeSignature -LiteralPath $InnoInstaller

    if (
        $Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        -not $Signature.SignerCertificate -or
        $Signature.SignerCertificate.Subject -notmatch "Pyrsys B\.V\."
    ) {
        throw (
            "The digital signature of the official Inno Setup installer was " +
            "not validated. The file will not be executed."
        )
    }

    $InnoArgs = @(
        "/PORTABLE=1",
        "/VERYSILENT",
        "/CURRENTUSER",
        "/NORESTART",
        "/SUPPRESSMSGBOXES",
        "/SP-",
        ("/DIR=" + $InnoRoot)
    )

    & $InnoInstaller @InnoArgs

    Assert-LastExit "Portable Inno Setup preparation"

    $Iscc = Join-Path $InnoRoot "ISCC.exe"

    if (-not (Test-Path -LiteralPath $Iscc)) {
        throw "ISCC.exe was not found after the portable extraction."
    }
}

Write-Step "Compiling the Inno installer of $ProductName $ProductVersion"

Write-Host "Root: $Root"
Write-Host "Inno script: $InstallerScript"

if (-not (Test-Path -LiteralPath $InstallerScript)) {
    throw "The .iss file was not found: $InstallerScript"
}

& $Iscc "/Qp" $InstallerScript

Assert-LastExit "Inno Setup"

if (-not (Test-Path -LiteralPath $InstallerOutput)) {
    throw "The expected installer was not produced: $InstallerOutput"
}

$SetupHash = (
    Get-FileHash -Algorithm SHA256 -LiteralPath $InstallerOutput
).Hash.ToLowerInvariant()

$HashFile = $InstallerOutput + ".sha256"

Set-Content -LiteralPath $HashFile -Value (
    "$SetupHash  " + [IO.Path]::GetFileName($InstallerOutput)
) -Encoding ASCII

Write-Host ""
Write-Host "SUCCESS" -ForegroundColor Green
Write-Host "Installer: $InstallerOutput"
if ($TempDriveLetter) {
    $RealOutput = Join-Path $Root (
        "dist\installer\" + [IO.Path]::GetFileName($InstallerOutput)
    )
    Write-Host "Real path: $RealOutput"
}
Write-Host "SHA-256: $SetupHash"
Write-Host "The Inno compiler and the build Python stayed inside this project."
if ($TempDriveLetter) {
    & subst ($TempDriveLetter + ":") /D
}
