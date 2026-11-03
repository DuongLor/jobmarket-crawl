# jobmarket-crawl

Thu thập tin tuyển dụng công khai (TopCV, VietnamWorks) mỗi đêm, làm sạch, chuẩn hoá và xem thống kê nhu cầu + mức lương trên dashboard chạy ở máy cá nhân. **Chỉ dùng cá nhân** để tham khảo, theo dõi — không chia sẻ hay công khai lại dữ liệu.

Dự án độc lập, **không liên quan CCareer** dù nằm cùng thư mục `D:\Project\ccareer-project\`.

> **Trạng thái (2026-11-03):** chạy được đủ Bước 1–5 (crawl, chuẩn hoá, thống kê, dashboard, báo cáo tháng, lịch chạy). **Crawl tự động: CareerLink** — danh sách tin (có ngày đăng/cập nhật, cấp bậc, lương, địa điểm); ngành lấy từ trang danh mục + trang chi tiết; kinh nghiệm chỉ ở trang chi tiết, hiện đang bị hộp xác minh "không phải robot" → dừng, không vượt, lịch chạy tự thử lại. **Dữ liệu lịch sử: VietJobs** ([HF dinhieufam/VietJobs](https://huggingface.co/datasets/dinhieufam/VietJobs), MIT) — xem ở bộ dữ liệu "Lịch sử". TopCV và JobsGO chặn bot → tắt; TopCV vẫn nạp được qua `import-html`. Chi tiết nguồn: [`docs/spike-0.md`](docs/spike-0.md).

**Dashboard** gồm: Tổng quan · Nhu cầu tuyển dụng (ngành / địa phương / tháng / bản đồ nhiệt) · Phân tích lương (kinh nghiệm / cấp bậc / địa phương / nghề / loại lương) · Xu hướng · Chất lượng & ánh xạ nhãn (duyệt nhãn ngay trên giao diện, có nhật ký). Bộ lọc dùng chung ở thanh bên, mọi bảng xuất CSV.

## Cách chạy

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # hoặc: pip install httpx selectolax typer pandas pytest ruff

.venv/Scripts/python -m jobmarket init              # tạo data/market.db + nạp danh mục chuẩn
.venv/Scripts/python -m jobmarket import-html       # nạp trang TopCV bạn tự lưu vào data/inbox/topcv/
.venv/Scripts/python -m jobmarket import-csv <file> # nạp CSV tin đã làm sạch (định dạng tab Data_Jobs)
.venv/Scripts/python -m jobmarket import-vietjobs   # nạp bộ dữ liệu lịch sử VietJobs (48.092 tin, 07–10/2025)
.venv/Scripts/python -m jobmarket remap             # áp lại quy tắc (vd sau khi thêm quận/huyện, tên ngành vào seeds)
.venv/Scripts/python -m jobmarket backfill          # điền ngày đăng còn trống từ thẻ tin đã lưu (không request mạng)
.venv/Scripts/python -m jobmarket enrich --limit 20 # chỉ chạy tầng chi tiết (ngành, kinh nghiệm, hạn nộp)
.venv/Scripts/python -m jobmarket summary           # tóm tắt nhanh dữ liệu
.venv/Scripts/python -m jobmarket dashboard         # mở dashboard http://localhost:8501 (dữ liệu thật)
.venv/Scripts/python -m jobmarket demo              # tạo data/demo.db từ 374 tin mẫu của mockup
.venv/Scripts/python -m jobmarket dashboard --demo  # dashboard trên dữ liệu mẫu (không đụng DB thật)
.venv/Scripts/python -m jobmarket run               # crawl mọi nguồn đang bật (hiện: CareerLink)
.venv/Scripts/python -m jobmarket run --max-pages 22 # lần đầu lấy nhiều (mỗi danh sách tối đa 22 trang)

.venv/Scripts/python -m pytest                      # test (gồm test thống kê khớp số mockup)
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
```

**Tự chạy (Windows Task Scheduler)** — chạy 1 lần trong **PowerShell** để đăng ký. Lịch 4 lần/ngày (02:00, 08:00, 14:00, 20:00) vì CareerLink giới hạn ~50 request mỗi khoảng thời gian; máy tắt lúc đó thì chạy bù khi bật:

```powershell
$dir = "D:\Project\ccareer-project\jobmarket-crawl"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\scripts\run_nightly.ps1`""
$trigger = @("2:00am", "8:00am", "2:00pm", "8:00pm" | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "jobmarket-crawl" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Crawl tin tuyển dụng hằng đêm"
```

Chạy thử ngay: `Start-ScheduledTask jobmarket-crawl` · xem kết quả: `Get-ScheduledTaskInfo jobmarket-crawl` (LastTaskResult 0 = ổn) · log: `data\logs\run_<ngày>.log` · gỡ: `Unregister-ScheduledTask jobmarket-crawl -Confirm:$false`.

**Lưu trang TopCV để nạp tay:** mở trang danh sách (vd `topcv.vn/tim-viec-lam-cong-nghe-thong-tin-cr257`) trong trình duyệt → Ctrl+S → chọn "Webpage, HTML only" → lưu vào `data/inbox/topcv/`. Ngành lấy từ tiêu đề trang; ngày thấy tin = lúc bạn lưu file. File đã nạp chuyển vào `done/`.

## Tài liệu

| File | Nội dung |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | Kế hoạch: nghiệp vụ, business rule BR-01…BR-11, kiến trúc, quy tắc chuẩn hoá, dữ liệu, dashboard, lộ trình, câu hỏi mở |
| [`docs/ui-lowfi.html`](docs/ui-lowfi.html) | Mockup giao diện low-fi (mở bằng trình duyệt) |
| [`docs/mockup.xlsx`](docs/mockup.xlsx) | Workbook mẫu: công thức thống kê, tham số, danh mục chuẩn, 374 dòng dữ liệu giả lập dùng làm dữ liệu kiểm thử |
| [`docs/ycau-goc.md`](docs/ycau-goc.md) | Yêu cầu gốc (viết cho C-Career, đã điều chỉnh trong PLAN §A2) |

## Stack (dự kiến)

Python 3.12 · httpx + selectolax · SQLite · pandas · Streamlit + Plotly · chạy hằng đêm bằng Windows Task Scheduler.

## Các mục sẽ bổ sung khi có code

Luồng dữ liệu · từng bảng/cột và vì sao có · quy tắc chuẩn hoá lương và nhãn · cách chạy và chạy lại · xử lý lỗi · xuất/nạp ánh xạ nhãn.
