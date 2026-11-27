# Registers (or refreshes) the 2-hourly Windows scheduled task.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = 'C:\Users\hongj\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
$taskName = 'FlightMonitor-SHA-HAK'
$action = New-ScheduledTaskAction -Execute $python -Argument 'run_cycle.py' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 2) `
    -RepetitionDuration (New-TimeSpan -Days 365)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description 'Shanghai to Haikou flight price monitor (all-in price, direct only)' -Force | Out-Null
Write-Output "registered task: $taskName"
