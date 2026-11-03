# Chạy crawl mọi nguồn đang bật — Task Scheduler gọi file này mỗi đêm.
# Log: data\logs\run_YYYY-MM-DD.log. Exit code ≠ 0 khi có nguồn thất bại (Task Scheduler ghi "Last Run Result").

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("run_" + (Get-Date -Format "yyyy-MM-dd") + ".log")

$env:PYTHONIOENCODING = "utf-8"
Add-Content -Path $log -Value ("=== start " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
& (Join-Path $root ".venv\Scripts\python.exe") -m jobmarket run 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
Add-Content -Path $log -Value ("=== end " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " exit=$code")
exit $code
