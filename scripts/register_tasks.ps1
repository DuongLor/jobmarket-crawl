# Dang ky Task Scheduler: moi nguon 1 task `run --source <nguon> --full`, 4 gio thu 1 lan.
#   Dang chay -> bo qua (IgnoreNew + `run` tu bo qua nguon co tien trinh khac dang doc).
#   Dang nghi sau khi bi chan -> `run` tu bo qua; het nghi -> doc tiep phan con do.
# Chay lai file nay de cap nhat. Go: .\scripts\register_tasks.ps1 -Remove
param([switch]$Remove)

$dir = Split-Path -Parent $PSScriptRoot
$sources = "vieclam24h", "careerviet"  # nguon dang bat trong config.toml (TopCV/JobsGO chan bot, VietnamWorks chua co)

if ($Remove) {
    Get-ScheduledTask -TaskName "jobmarket-full-*" -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    "Da go cac task jobmarket-full-*"
    exit 0
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 3) -MultipleInstances IgnoreNew
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 4)
# Go task cua nguon da tat (vd careerlink/itviec tu 2026-11-12) truoc khi dang ky lai
Get-ScheduledTask -TaskName "jobmarket-full-*" -ErrorAction SilentlyContinue |
    Where-Object { $sources -notcontains ($_.TaskName -replace '^jobmarket-full-', '') } |
    Unregister-ScheduledTask -Confirm:$false
foreach ($s in $sources) {
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\scripts\run_nightly.ps1`" -Source $s -Full"
    Register-ScheduledTask -TaskName "jobmarket-full-$s" -Action $action -Trigger $trigger -Settings $settings `
        -Description "jobmarket: quet tron $s (--full), tu nghi khi bi chan" -Force | Out-Null
}
Get-ScheduledTask -TaskName "jobmarket-full-*" | Select-Object TaskName, State | Format-Table -AutoSize
"Log: $dir\data\logs\run_<nguon>_<ngay>.log"
