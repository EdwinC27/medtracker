# Removes the MedTracker auto-start scheduled task.
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall_autostart.ps1

$taskName = 'MedTracker'

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Scheduled task '$taskName' removed." -ForegroundColor Green
} else {
    Write-Host "No scheduled task named '$taskName' was found."
}
