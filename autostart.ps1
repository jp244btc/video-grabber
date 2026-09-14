<#
    Video Grabber - run hidden at sign-in.

    Creates a shortcut in the Startup folder pointing at pythonw.exe, the
    console-free Python launcher. No terminal window, no admin rights, and the
    shortcut is a plain file you can delete by hand if you ever want it gone.

    Enable :  powershell -ExecutionPolicy Bypass -File autostart.ps1
    Disable:  powershell -ExecutionPolicy Bypass -File autostart.ps1 -Remove
#>

param([switch]$Remove)

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "Video Grabber.lnk"

if ($Remove) {
    if (Test-Path -LiteralPath $link) {
        Remove-Item -LiteralPath $link -Force
        Write-Host "Autostart removed." -ForegroundColor Green
        Write-Host "The copy running right now keeps running. It will not come back at next sign-in."
    } else {
        Write-Host "Autostart was not enabled - nothing to remove."
    }
    return
}

$pythonw = Join-Path $here ".venv\Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Host "ERROR: could not find $pythonw" -ForegroundColor Red
    Write-Host "Run run.bat once first so the Python environment gets built."
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"app.py" --no-browser'
$shortcut.WorkingDirectory = $here
$shortcut.Description = "Video Grabber downloader (runs hidden)"
$shortcut.Save()

Write-Host "Autostart enabled." -ForegroundColor Green
Write-Host "  Shortcut: $link"

# If a copy is already up it is almost certainly the console window from
# run.bat. Starting another would just exit on the port check, and reporting
# "running" would wrongly suggest the hidden copy had taken over.
$before = Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue
if ($before) {
    Write-Host ""
    Write-Host "  A copy is already running - almost certainly the console window" -ForegroundColor Yellow
    Write-Host "  from run.bat. That one still owns the port, so nothing changes yet."
    Write-Host ""
    Write-Host "  Close that black window, then either run this again or just sign"
    Write-Host "  out and back in. The hidden copy takes over from then on."
    return
}

# Otherwise start it now, so there is no need to sign out and back in.
Start-Process -FilePath $pythonw -ArgumentList '"app.py"', '--no-browser' `
    -WorkingDirectory $here -WindowStyle Hidden

Start-Sleep -Seconds 3

$listening = Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host "  Running now, hidden, on http://127.0.0.1:5001" -ForegroundColor Green
} else {
    Write-Host "  Started, but nothing is listening on 5001 yet." -ForegroundColor Yellow
    Write-Host "  Check video-grabber.log in this folder."
}
