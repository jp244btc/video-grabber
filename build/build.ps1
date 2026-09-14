<#
    Build the packaged Windows app and its installer.

        powershell -ExecutionPolicy Bypass -File build\build.ps1

    Produces:
        dist\VideoGrabber\                 the frozen app (portable, run VideoGrabber.exe)
        dist\VideoGrabber-Setup-<ver>.exe  the installer

    Needs Python 3.10+ on PATH and Inno Setup 6 (winget install JRSoftware.InnoSetup).
    Everything Python-side goes into a throwaway .build-venv so the machine's
    own Python is never touched.
#>

param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# --- version comes from app.py so there is exactly one place to bump it ----
$version = [regex]::Match((Get-Content "app.py" -Raw), 'VERSION\s*=\s*"([^"]+)"').Groups[1].Value
if (-not $version) { throw "Could not read VERSION from app.py" }
Write-Host "Building Video Grabber $version" -ForegroundColor Cyan

# --- python -----------------------------------------------------------------
$py = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    try {
        $v = & cmd /c "$candidate --version 2>&1"
        if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[0-9]|[2-9][0-9])") { $py = $candidate; break }
    } catch {}
}
if (-not $py) { throw "Python 3.10 or newer was not found on PATH." }

if (-not (Test-Path ".build-venv\Scripts\python.exe")) {
    Write-Host "[1/5] Creating build environment..."
    & cmd /c "$py -m venv .build-venv"
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
$venvPy = Join-Path $root ".build-venv\Scripts\python.exe"

Write-Host "[2/5] Installing dependencies..."
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- icons -----------------------------------------------------------------
Write-Host "[3/5] Generating icons..."
& $venvPy extension\make_icons.py | Out-Null

# --- freeze ------------------------------------------------------------------
Write-Host "[4/5] Freezing with PyInstaller..."
if (Test-Path "dist\VideoGrabber") { Remove-Item "dist\VideoGrabber" -Recurse -Force }
& $venvPy -m PyInstaller --noconfirm --clean --log-level WARN VideoGrabber.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# yt_dlp rides along as a plain folder so the app can update it in place.
$site = & $venvPy -c "import yt_dlp, os; print(os.path.dirname(yt_dlp.__file__))"
Copy-Item $site "dist\VideoGrabber\yt_dlp" -Recurse -Force
Get-ChildItem "dist\VideoGrabber\yt_dlp" -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Host "    dist\VideoGrabber ready" -ForegroundColor Green

if ($SkipInstaller) { return }

# --- installer ---------------------------------------------------------------
Write-Host "[5/5] Building installer..."
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source }
}
if (-not $iscc) { throw "Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup" }

& $iscc /Q "/DAppVersion=$version" "installer\VideoGrabber.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

$setup = Get-Item "dist\VideoGrabber-Setup-$version.exe"
Write-Host ("    {0}  ({1:N1} MB)" -f $setup.FullName, ($setup.Length / 1MB)) -ForegroundColor Green
