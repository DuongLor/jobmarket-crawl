# Chạy crawl — Task Scheduler gọi file này (xem README "Tự chạy").
#   Mặc định: mọi nguồn đang bật, có giới hạn mỗi lần (6 lần/ngày).
#   -Source <tên> -Full: quét trọn 1 nguồn, đọc tiếp phần còn dở; bị chặn → `run` tự bỏ qua tới hết thời gian nghỉ.
# Không dùng cùng lúc với `jobmarket start`. Log: data\logs\run_[<nguồn>_]YYYY-MM-DD.log. Exit code ≠ 0 khi có nguồn
# thất bại (Task Scheduler ghi "Last Run Result").
param(
    [string]$Source = "all",
    [switch]$Full
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$tag = if ($Source -eq "all") { "run_" } else { "run_" + $Source + "_" }
$log = Join-Path $logDir ($tag + (Get-Date -Format "yyyy-MM-dd") + ".log")

$env:PYTHONIOENCODING = "utf-8"
$python = Join-Path $root ".venv\Scripts\python.exe"
$runArgs = @("-m", "jobmarket", "run", "--source", $Source)
if ($Full) { $runArgs += "--full" }

Add-Content -Path $log -Value ("=== start " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + ($runArgs -join " "))
# Nap trang TopCV ban tu luu (data\inbox\topcv) truoc khi crawl - khong request mang
if ($Source -eq "all") {
    & $python -m jobmarket import-html 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
}
& $python @runArgs 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
$code = $LASTEXITCODE
Add-Content -Path $log -Value ("=== end " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " exit=$code")
exit $code
