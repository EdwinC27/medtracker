# ---------------------------------------------------------------------------
#  MedTracker - register a Windows scheduled task that starts the app at logon.
#
#  This is the piece that makes reminders work with no browser open: Windows
#  starts MedTracker (web server + background scheduler) when you sign in, and
#  the scheduler sends desktop toasts from then on.
#
#  Run in PowerShell:
#      powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
#
#  Remove it again with:
#      powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1
#
#  No administrator rights are required: the task is registered for the
#  current user only.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'

$taskName    = 'MedTracker'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher    = Join-Path $projectRoot 'scripts\start_hidden.vbs'

if (-not (Test-Path $launcher)) {
    Write-Error "Launcher not found: $launcher"
    exit 1
}

if (-not (Test-Path (Join-Path $projectRoot '.venv\Scripts\python.exe'))) {
    Write-Warning 'The virtual environment is missing. Run scripts\install.bat first.'
}

$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
    -Argument ('"{0}"' -f $launcher) -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description 'Starts MedTracker (web app + reminder scheduler) at logon.' | Out-Null

Write-Host ''
Write-Host "Scheduled task '$taskName' registered for user $env:USERNAME." -ForegroundColor Green
Write-Host 'MedTracker will now start automatically when you sign in to Windows.'
Write-Host 'Open it at http://127.0.0.1:8000'
Write-Host ''
Write-Host 'Start it right now without signing out:' -ForegroundColor Cyan
Write-Host "    Start-ScheduledTask -TaskName $taskName"
