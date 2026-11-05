# jobmarket-crawl

Thu thập tin tuyển dụng công khai (CareerLink, Việc Làm 24h, CareerViet; TopCV qua trang bạn tự lưu) nhiều lần mỗi ngày, làm sạch, chuẩn hoá và xem thống kê nhu cầu + mức lương trên dashboard chạy ở máy cá nhân. **Chỉ dùng cá nhân** để tham khảo, theo dõi — không chia sẻ hay công khai lại dữ liệu.

Dự án độc lập, **không liên quan CCareer** dù nằm cùng thư mục `D:\Project\ccareer-project\`.

> **Trạng thái (2026-11-04):** crawl tự động **CareerLink, Việc Làm 24h, CareerViet** (thêm 2 nguồn mới 2026-11-04 — Việc Làm 24h chỉ đọc được trang 1 của mỗi danh sách, trang 2 trả 403 → không cố; CareerViet robots cấm trang chi tiết `/vi/jobs/*` → chỉ thẻ tin). Thống kê đã sửa 2026-11-04: "Đang tuyển" chỉ đếm tin xác định được trạng thái; tin đăng trước ngày bắt đầu thu thập không tính vào xu hướng; cơ cấu ngành đếm chia đều tin nhiều ngành và không dùng ngành suy từ trang danh mục.
>
> **Trạng thái (2026-11-03):** chạy được đủ Bước 1–5 (crawl, chuẩn hoá, thống kê, dashboard, báo cáo tháng, lịch chạy). **Crawl tự động: CareerLink** — danh sách tin (có ngày đăng/cập nhật, cấp bậc, lương, địa điểm); ngành lấy từ trang danh mục + trang chi tiết; kinh nghiệm chỉ ở trang chi tiết, hiện đang bị hộp xác minh "không phải robot" → dừng, không vượt, lịch chạy tự thử lại. **Dữ liệu lịch sử: VietJobs** ([HF dinhieufam/VietJobs](https://huggingface.co/datasets/dinhieufam/VietJobs), MIT) — xem ở bộ dữ liệu "Lịch sử". TopCV và JobsGO chặn bot → tắt; TopCV vẫn nạp được qua `import-html`. Chi tiết nguồn: [`docs/spike-0.md`](docs/spike-0.md).

**Dashboard** gồm: Tổng quan · Danh sách tin · Nhu cầu tuyển dụng (ngành / địa phương / tháng / bản đồ nhiệt) · Phân tích lương (kinh nghiệm / cấp bậc / địa phương / nghề / loại lương) · Xu hướng · Báo cáo tháng · Chất lượng & ánh xạ nhãn (duyệt nhãn ngay trên giao diện, có nhật ký). Bộ lọc dùng chung ở thanh bên, mọi bảng xuất CSV.

## Chạy hằng ngày — 1 lệnh

```bash
cd /d/Project/ccareer-project/jobmarket-crawl && .venv/Scripts/python -m jobmarket start
```

Mở dashboard `http://127.0.0.1:8501` (chỉ máy này), tự nạp trang TopCV bạn lưu vào `data/inbox/topcv/`, crawl **mọi nguồn đang bật**, mỗi nguồn
cách nhau ≥ 4 giờ tính từ lần chạy cuối trong DB (tắt/mở lại không crawl dồn). Để cửa sổ chạy; dừng bằng Ctrl+C.
Log: `data/logs/start_<ngày>.log`. Tuỳ chọn: `--now` (crawl ngay khi mở, bỏ qua khoảng cách lần đầu — chỉ dùng khi
chắc lần chạy trước đã cách ≥ 4 giờ), `--every-hours 6`, `--no-crawl`, `--port 8502`, `--no-open-browser`.

⚠️ **Chọn MỘT cách chạy:** `start` **hoặc** Task Scheduler (dưới). `run` cũng tự bỏ qua nguồn đã chạy trong 3,5 giờ
qua (`--force` để ép), nên chạy lẫn không crawl dồn — nhưng giữ một cách cho dễ theo dõi.
**Sau khi cập nhật code:** tắt cửa sổ `start` (Ctrl+C) rồi mở lại — tiến trình đang chạy vẫn dùng code cũ.

## Cách chạy (từng lệnh)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"    # cài theo pyproject.toml (httpx, selectolax, typer, pandas, streamlit, plotly, openpyxl, pytest, ruff)

.venv/Scripts/python -m jobmarket init              # tạo data/market.db + nạp danh mục chuẩn
.venv/Scripts/python -m jobmarket import-html       # nạp trang TopCV bạn tự lưu vào data/inbox/topcv/
.venv/Scripts/python -m jobmarket import-csv <file> # nạp CSV tin đã làm sạch (định dạng tab Data_Jobs)
.venv/Scripts/python -m jobmarket import-vietjobs   # nạp bộ lịch sử VietJobs (48.092 dòng → 34.151 tin sau bỏ trùng, 07–10/2025)
.venv/Scripts/python -m jobmarket remap             # áp lại quy tắc sau khi sửa seeds (nhãn tự động đi theo seeds; nhãn sửa tay giữ nguyên)
.venv/Scripts/python -m jobmarket backfill --source careerviet  # tính lại từ bản gốc (không mạng): ngày đăng, ngành chi tiết, mục ngành
.venv/Scripts/python -m jobmarket renormalize       # tính lại lương (mọi nguồn) từ chuỗi gốc sau khi đổi ngưỡng/quy tắc (không mạng)
.venv/Scripts/python scripts/scrub_raw.py [--apply] # quét lại PII trong bản gốc đã lưu (chạy thử, rồi --apply)
.venv/Scripts/python scripts/audit_careerlink.py    # đối chiếu DB với thẻ tin đã lưu, báo số lệch từng trường
.venv/Scripts/python -m jobmarket enrich --limit 20 # chỉ chạy tầng chi tiết CareerLink (ngành, kinh nghiệm, hạn nộp)
.venv/Scripts/python -m jobmarket watch             # chỉ tự nạp trang TopCV bạn lưu (không request mạng)
.venv/Scripts/python -m jobmarket report 2026-11 [--publish]  # báo cáo tháng; --publish chốt + Excel vào data/reports/
.venv/Scripts/python -m jobmarket summary           # tóm tắt nhanh dữ liệu
.venv/Scripts/python -m jobmarket health            # chuỗi ngày liên tục có dữ liệu theo nguồn (mốc ≥ 7 ngày), lần chạy kẹt
.venv/Scripts/python -m jobmarket mysql-sync        # chép DB sang MySQL schema `jobmarket` để xem bằng Workbench (tự chạy sau run/start nếu [mysql] enabled)
.venv/Scripts/python -m jobmarket audit             # 30 tin ngẫu nhiên/nguồn → data/audit/*.xlsx để đối chiếu trang thật; --score <file> chấm
.venv/Scripts/python -m jobmarket dashboard         # mở dashboard http://127.0.0.1:8501 (chỉ máy này, dữ liệu thật)
.venv/Scripts/python -m jobmarket demo              # tạo data/demo.db từ 374 tin mẫu của mockup
.venv/Scripts/python -m jobmarket dashboard --demo  # dashboard trên dữ liệu mẫu (không đụng DB thật)
.venv/Scripts/python -m jobmarket run               # crawl mọi nguồn đang bật (careerlink, vieclam24h, careerviet); bỏ qua nguồn chạy < 3,5 giờ trước
.venv/Scripts/python -m jobmarket run --max-pages 22 # lần đầu lấy nhiều (mỗi danh sách tối đa 22 trang)

.venv/Scripts/python -m pytest                      # test (gồm test thống kê khớp số mockup)
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
```

**Tự chạy (Windows Task Scheduler)** — chạy 1 lần trong **PowerShell** để đăng ký. Lịch 6 lần/ngày, cách 4 giờ (01, 05, 09, 13, 17, 21 giờ): CareerLink hạn chế ngầm khi đọc dày (~25 request/phút hoặc ~55/9 phút, tự gỡ sau ~3,5 giờ) → mỗi lần ≤ 40 trang danh sách + 10 trang chi tiết, cách ≥ 4 giờ; quét trọn ~34.400 tin mất khoảng 5 ngày. (2026-11-04: chưa đăng ký — đang dùng `start`.) Máy tắt lúc đó thì chạy bù khi bật:

```powershell
$dir = "D:\Project\ccareer-project\jobmarket-crawl"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $dir `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$dir\scripts\run_nightly.ps1`""
$trigger = @("1:00am", "5:00am", "9:00am", "1:00pm", "5:00pm", "9:00pm" | ForEach-Object { New-ScheduledTaskTrigger -Daily -At $_ })
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "jobmarket-crawl" -Action $action -Trigger $trigger -Settings $settings `
  -Description "Crawl tin tuyển dụng hằng đêm"
```

Chạy thử ngay: `Start-ScheduledTask jobmarket-crawl` · xem kết quả: `Get-ScheduledTaskInfo jobmarket-crawl` (LastTaskResult 0 = ổn) · log: `data\logs\run_<ngày>.log` · gỡ: `Unregister-ScheduledTask jobmarket-crawl -Confirm:$false`.

**TopCV — lưu trang để nạp tay** (TopCV chặn bot bằng Cloudflare, kể cả sitemap; dự án không vượt chặn). Khi duyệt TopCV bằng trình duyệt của bạn, Ctrl+S → "Webpage, HTML only" → lưu vào `data/inbox/topcv/`, rồi `python -m jobmarket import-html`:
- **Trang danh sách** (vd `topcv.vn/tim-viec-lam-cong-nghe-thong-tin-cr257`): ~50 tin/trang — tiêu đề, công ty, lương, địa điểm, kinh nghiệm; ngành lấy từ tiêu đề trang.
- **Trang chi tiết tin** (`topcv.vn/viec-lam/…/<mã>.html`): đầy đủ nhất — đọc JSON-LD `JobPosting` của trang: ngành, kinh nghiệm, ngày đăng gốc, hạn nộp, loại hình, mô tả (đã xoá SĐT/email). Gộp vào đúng tin đã có từ trang danh sách (cùng mã tin), không tạo tin trùng.

Ngày thấy tin = lúc bạn lưu file. File đã nạp chuyển vào `done/`. **Tự nạp:** `python -m jobmarket watch` theo dõi thư mục, lưu trang xong ~10–20 giây là vào DB (file không hợp lệ báo 1 lần, để nguyên chỗ); lịch chạy (`run_nightly.ps1`) cũng nạp inbox trước khi crawl.

## Tài liệu

| File | Nội dung |
|---|---|
| [`docs/PLAN.md`](docs/PLAN.md) | Kế hoạch: nghiệp vụ, business rule BR-01…BR-11, kiến trúc, quy tắc chuẩn hoá, dữ liệu, dashboard, lộ trình, câu hỏi mở |
| [`docs/ui-lowfi.html`](docs/ui-lowfi.html) | Mockup giao diện low-fi (mở bằng trình duyệt) |
| [`docs/mockup.xlsx`](docs/mockup.xlsx) | Workbook mẫu: công thức thống kê, tham số, danh mục chuẩn, 374 dòng dữ liệu giả lập dùng làm dữ liệu kiểm thử |
| [`docs/ycau-goc.md`](docs/ycau-goc.md) | Yêu cầu gốc (viết cho C-Career, đã điều chỉnh trong PLAN §A2) |

## Stack

Python ≥ 3.12 (venv hiện 3.14) · httpx + selectolax · SQLite (migration `migrations/001–007`) · pandas · Streamlit + Plotly · chạy bằng `start` hoặc Windows Task Scheduler.

## Quy tắc thu thập (không đổi khi thêm nguồn)

- robots.txt theo RFC 9309 (`jobmarket/fetch/robots.py`, hiểu `*`/`$`), kiểm cả đích chuyển hướng; User-Agent tự khai danh tính; ≥ 10 giây/request mỗi nguồn.
- 403 / 429 / trang xác minh "không phải robot" / trang trống giữa chừng → **dừng nguồn đó**, không thử vượt (không đổi UA, không proxy, không trình duyệt tự động).
- Bản gốc (`data/raw`) chỉ giữ trường cần cho thống kê; lớp chặn chung `store/raw.sanitize` bỏ mọi key liên hệ (email/phone/username/contact…) và xoá email/SĐT/link MXH trong chuỗi trước khi ghi đĩa.
