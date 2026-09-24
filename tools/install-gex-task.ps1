# Registers the GEX snapshot collector (tools/gex_collect.py) with Windows Task
# Scheduler: every 15 minutes, for the current user, only while logged on (so no
# stored password). The script gates itself on the ET clock, so most off-hours
# runs exit without touching the network.
#
#   pwsh tools/install-gex-task.ps1            # install / replace
#   pwsh tools/install-gex-task.ps1 -Remove    # uninstall
#
# Replaces the WSL cron entry that stopped when the project moved to Windows.
param([switch]$Remove)

$name = "atas-journal GEX collect"
$root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed '$name'"
    return
}

# pythonw: no console window flashing up every quarter hour. The script writes
# its own log (data/cache/gex/collect.log), so nothing is lost by having no stdout.
$py = Join-Path $root ".venv\Scripts\pythonw.exe"
$script = Join-Path $root "tools\gex_collect.py"
if (-not (Test-Path $py)) { throw "no venv interpreter at $py" }

$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`"" -WorkingDirectory $root
# A repeating one-off trigger with no end runs indefinitely; start on the next
# quarter hour so runs line up with :00/:15/:30/:45.
$now = Get-Date
$start = $now.Date.AddHours($now.Hour).AddMinutes(15 * [math]::Ceiling(($now.Minute + 1) / 15))
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 15)
# StartWhenAvailable: a box that was asleep or rebooting catches up on wake
# instead of silently skipping — a missed day can never be backfilled.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Banks Cboe delayed NDX/QQQ option chains every 15 min (tools/gex_collect.py)" `
    -Force | Out-Null
Write-Output "installed '$name' — first run $start, every 15 min"
