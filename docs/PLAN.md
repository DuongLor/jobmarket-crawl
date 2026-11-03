# Plan — Dự án độc lập `jobmarket-crawl/`: Crawl & thống kê thị trường tuyển dụng (dùng cá nhân để tham khảo, theo dõi)

> **Trạng thái:** plan v2, chưa code · 2026-11-03 · **Đã chốt: chỉ dùng cá nhân** (1 người dùng, chạy máy cá nhân, không chia sẻ/công khai dữ liệu). Q1–Q7 tạm theo cột "Đề xuất" ở B11 cho tới khi user đổi.
> **Đổi so với v1:** v1 tích hợp vào CCareer (DB, quyền, màn admin trong `ccareer/` + `ccareerfe/`). Theo yêu cầu mới, dự án **tách hẳn**: không dùng code, DB, master data, quyền hay quy ước của CCareer. Bản v1 lưu ở `_trash/task24-plan.v1-tich-hop-ccareer.md`.
> **Nguồn (đều trong `docs/`):** [`ycau-goc.md`](./ycau-goc.md) (yêu cầu gốc viết cho C-Career) · [`mockup.xlsx`](./mockup.xlsx) (workbook 14 tab) · [`ui-lowfi.html`](./ui-lowfi.html) (mockup giao diện, B6)
> **Vị trí:** `D:\Project\ccareer-project\jobmarket-crawl\` — git repo riêng (nhánh `develop`), không liên quan CCareer (giống `cbase/`). Tạo 2026-11-03.

---

## Phần A — Phân tích nghiệp vụ (theo khuôn `pm`)

**Tóm tắt:** Một người dùng (bạn) · muốn tự động thu tin tuyển dụng công khai từ TopCV + VietnamWorks mỗi ngày, làm sạch, và xem thống kê nhu cầu + mức lương · để **tham khảo và theo dõi thị trường**, không phục vụ khách hàng hay hệ thống nào.

### A1. Mục tiêu & thước đo thành công
- Mỗi sáng có số liệu mới của đêm trước, không phải thao tác tay.
- Mở 1 trang là thấy: tổng quan, nhu cầu theo ngành/tỉnh/tháng, lương theo kinh nghiệm/cấp bậc/tỉnh/nghề, xu hướng, chất lượng dữ liệu.
- Chất lượng tối thiểu (lấy từ tab Quality của mockup): reject ≤ 2%, có địa điểm ≥ 95%, có ngành ≥ 90%, có nghề ≥ 80%, có kinh nghiệm ≥ 90%.
- Đối chiếu tay 30 tin/nguồn với trang thật: ≥ 95% trường đúng.

### A2. Giữ / bỏ / đơn giản hoá so với `ycau.md` (viết cho C-Career)

| Yêu cầu gốc | Bản độc lập | Lý do |
|---|---|---|
| 2 nguồn, chạy hằng ngày ngoài giờ cao điểm | **Giữ** | Cốt lõi |
| 14 trường mỗi tin, không lấy thông tin cá nhân, giữ bản gốc | **Giữ** — xoá thông tin cá nhân **trước khi** lưu bản gốc | "Giữ bản gốc" và "không lấy PII" mâu thuẫn nếu lưu HTML thô |
| Chuẩn hoá lương, 6 nhóm kinh nghiệm, 7 cấp bậc, cờ ok/warn/reject, khoá trùng, view tin duy nhất | **Giữ nguyên quy tắc** | Đây là phần làm số liệu đúng |
| Ngành/nghề/tỉnh "về tên chuẩn của CCareer" | **Đổi:** danh mục chuẩn **riêng của dự án**, khởi tạo từ tab Master của Excel (12 ngành, ~30 nghề) + 34 tỉnh sau sáp nhập | Không phụ thuộc CCareer |
| Ánh xạ nhãn bằng CSV (xuất/nạp, file lỗi, chạy lại không trùng) | **Giữ** + thêm sửa nhanh ngay trên dashboard | Vẫn là cách nhanh nhất để gán hàng loạt |
| Phân quyền BA/PM/Tech Lead/Admin, chỉ Tech Lead thêm tên chuẩn | **Bỏ** | Một người dùng |
| Nhật ký thay đổi nhãn (người, thời điểm, cũ → mới, lý do) | **Giữ dạng nhẹ** (không có "người") | Rẻ, giúp lần lại vì sao số liệu đổi |
| 6 màn hình + bộ lọc chung + xuất CSV + chân trang | **Giữ**, làm bằng dashboard đơn giản | — |
| Báo cáo tháng PDF/Excel, báo cáo đã phát hành giữ nguyên | **Giữ dạng snapshot Excel**, PDF để sau | Excel đủ để tham khảo; snapshot đảm bảo số cũ không đổi |
| Ngưỡng 5 / 10 / 10% không viết cứng | **Giữ** (file cấu hình) | — |
| README cho cả nhóm | **Giữ, gọn hơn** (cho chính bạn sau 3 tháng đọc lại) | — |
| So sánh VN ↔ Nhật | **Ngoài phạm vi** | Như yêu cầu gốc |
| Kỹ năng (skills) | Lưu nguyên văn nếu nguồn có; **thống kê kỹ năng để Phase 2** | Cần từ điển kỹ năng (mockup ghi Phase 2) |

### A3. Business rules giữ lại (đánh số để test)
- **BR-01** Đơn vị đếm = số tin duy nhất (không phải số người cần tuyển), ghi rõ trên mọi màn.
- **BR-02** Tin reject: thiếu mã tin, tiêu đề hoặc tên công ty. Vẫn lưu, không tính thống kê.
- **BR-03** Khoá trùng = tên công ty + tiêu đề + tỉnh (đã chuẩn hoá). Không xoá tin trùng; view giữ tin thấy sớm nhất.
- **BR-04** Mọi thống kê chỉ đọc view `v_jobs_unique`.
- **BR-05** Nhãn chưa biết: không đoán, lưu trạng thái `unmapped`; tin chưa ánh xạ ngành hiện ở nhóm "(chưa phân loại)".
- **BR-06** Lương đại diện: khoảng → trung điểm; một mức → chính nó; "tới X", "từ X", thoả thuận, không ghi → không vào TB/trung vị.
- **BR-07** Nhóm có < 5 tin có lương số → hiện "n<5"; luôn hiện số tin + % có lương cạnh số liệu lương.
- **BR-08** Xu hướng: 3 tháng gần nhất vs 3 tháng liền trước; lệch > 10% → tăng/giảm; 3 tháng trước < 10 tin → "Chưa đủ dữ liệu".
- **BR-09** Chân trang mọi màn: nguồn, ngày chốt số liệu, đơn vị là số tin, lương là mức đăng tuyển chưa phân biệt Gross/Net.
- **BR-10** Ánh xạ nhãn có hiệu lực ngay, không cần crawl lại; báo cáo đã chốt không đổi theo.
- **BR-11** Ngưỡng (5, 10, 10%) và tham số (tỷ giá, khoảng lương hợp lệ…) nằm trong file cấu hình.

### A4. Rủi ro & câu hỏi nghiệp vụ
- **Điều khoản sử dụng của 2 trang** có thể cấm thu thập tự động — dùng cá nhân giảm rủi ro nhưng không xoá nó. Cách làm: tôn trọng `robots.txt`, tốc độ thấp, không đăng nhập, không vượt cơ chế chặn, không công khai lại dữ liệu.
- **Thiên lệch giai đoạn đầu:** trước ngày bắt đầu crawl chỉ thấy tin chưa hết hạn → các tháng đầu bị thiếu; xu hướng cần ≥ 6 tháng chạy liên tục.

---

## Phần B — Phân tích kỹ thuật (theo khuôn `dev`)

**Loại:** dự án mới · **Repo:** `jobmarket-crawl/` (độc lập) · **Độ lớn:** M (MVP ~3–4 tuần làm bán thời gian)

### B1. Quyết định kiến trúc (ADR-1)

| Vấn đề | Chọn | Lý do | Loại bỏ |
|---|---|---|---|
| Ngôn ngữ | **Python 3.12**, 1 project | Crawl + xử lý dữ liệu + dashboard cùng 1 ngôn ngữ | Laravel + Next.js như v1: quá nặng cho 1 người dùng |
| Lưu trữ | **SQLite** (1 file `data/market.db`, chế độ WAL) | Không cần server DB; hỗ trợ view + window function; vài trăm nghìn tin vẫn nhanh; sao lưu = copy 1 file | MySQL/Postgres: thêm việc vận hành không cần thiết |
| Bản gốc | File `data/raw/<nguồn>/<yyyy-mm>/<mã tin>.json.gz` (đã xoá PII) + sha256 trong DB | Giữ DB nhỏ; chạy lại chuẩn hoá không cần crawl lại | Cột JSON trong DB: phình nhanh |
| Thu thập | `httpx` + `selectolax`; **Playwright chỉ khi spike chứng minh cần** | Nhẹ, nhanh | Selenium |
| Dashboard | **Streamlit** + Plotly (biểu đồ, bản đồ nhiệt), bộ lọc ở sidebar, nút tải CSV mỗi bảng | Dựng 6 màn nhanh nhất, chạy `localhost` | Next.js riêng: tốn công; Metabase: thêm 1 server Java |
| Tính toán thống kê | SQL trên view + **pandas** cho trung vị/P25/P75 | SQLite không có hàm phân vị; pandas làm gọn | Tự viết phân vị bằng SQL |
| Lên lịch | **Windows Task Scheduler** chạy `python -m jobmarket run` lúc 02:00 ❓Q2 | Máy bạn là Windows; không cần Docker | cron trong Docker: cần Docker chạy thường trực |
| Báo cáo tháng | Snapshot JSON trong DB + xuất **Excel theo bố cục mockup** (openpyxl) | Mockup đã là khuôn báo cáo | PDF (WeasyPrint khó cài trên Windows) → Phase sau |
| Ánh xạ nhãn | Tin lưu **id nhãn gốc**; view nối nhãn → tên chuẩn lúc truy vấn | BR-10 tự đúng, không phải chạy lại gì | Ghi thẳng mã chuẩn vào tin |

### B2. Kiến trúc

```
Task Scheduler 02:00 ──▶ python -m jobmarket run
   fetch (robots, ≤1 req/2–3s) → xoá PII → raw .json.gz → parse (adapter/nguồn)
   → chuẩn hoá (lương, KN, cấp bậc, nhãn, tỉnh) → cờ chất lượng + khoá trùng
   → upsert SQLite: runs · companies · job_posts · labels      ┐
                                                               ├─▶ view v_jobs_unique
streamlit run dashboard/app.py  (localhost:8501) ◀── đọc ──────┘
   6 màn · bộ lọc chung · tải CSV · sửa nhãn · chốt báo cáo tháng → Excel
```

### B3. Cấu trúc thư mục

```
jobmarket-crawl/
  jobmarket/
    __main__.py          # CLI (typer): run · renormalize · labels-export · labels-import · report · doctor
    config.py            # đọc config.toml + .env
    fetch/client.py      # httpx: rate limit, retry/backoff, User-Agent, cache robots.txt
    sources/base.py      # interface: list_pages(), parse_list(), parse_detail()
    sources/topcv.py
    sources/vietnamworks.py
    pii.py               # xoá email, SĐT, người liên hệ
    normalize/salary.py · experience.py · level.py · location.py · labels.py · text.py
    quality.py · dedup.py
    store/db.py          # SQLite (sqlite3 chuẩn), migrations/*.sql đánh số
    store/raw.py         # ghi/đọc .json.gz
    stats.py             # mọi công thức thống kê (dashboard + báo cáo dùng chung)
    report.py            # snapshot tháng → Excel
  dashboard/app.py + pages/1_Tong_quan.py … 6_Chat_luong_va_nhan.py
  migrations/001_init.sql …
  seeds/industries.csv · occupations.csv · provinces.csv · province_aliases.csv · experiences.csv · levels.csv
  config.toml            # ngưỡng + tham số (BR-11)
  data/                  # market.db + raw/ + exports/ — KHÔNG commit
  tests/fixtures/<nguồn>/… · test_*.py
  README.md · pyproject.toml · .env.example · .gitignore
```

### B4. Chuẩn hoá (quy tắc chi tiết)

**Lương → VND/tháng**

| Chuỗi nguồn (ví dụ) | `salary_type` | from / to | Vào TB/trung vị? |
|---|---|---|---|
| `10 - 15 triệu`, `$800 - $1,200` | `range` | 10M / 15M | ✅ trung điểm |
| `15 triệu` | `fixed` | 15M / 15M | ✅ |
| `Tới 20 triệu`, `Up to $2,000` | `up_to` | – / 20M | ❌ |
| `Từ 10 triệu`, `Trên 10 triệu` | `from` | 10M / – | ❌ |
| `Thoả thuận`, `Cạnh tranh` | `negotiable` | – | ❌ |
| (không có) | `not_stated` | – | ❌ |

- Đơn vị `triệu/tr/M`, `nghìn/k`, số đầy đủ; USD × `fx_usd_vnd` (config); theo năm ÷ 12; tuần/giờ → hệ số trong config.
- Ngoài [3.000.000; 500.000.000] → giữ chuỗi gốc, from/to rỗng, cờ `salary_invalid`; không xác định được dạng → `salary_ambiguous`.
- `salary_tax`: `gross`/`net` nếu ghi rõ, còn lại `unknown`.

**Kinh nghiệm → 6 nhóm** theo mức tối thiểu: `NONE` · `LT_1Y` (<12 tháng) · `Y1_2` · `Y2_3` · `Y3_5` · `Y5P` (≥ 60 tháng). "2–4 năm" → `Y2_3`. Không đọc được → rỗng + cờ `experience_unparsed`.

**Cấp bậc → 7 cấp** `INTERN · JUNIOR · STAFF · SENIOR · LEAD · MANAGER · DIRECTOR`: (1) trường cấp bậc của nguồn qua bảng nhãn; (2) nếu trống — bảng từ khoá cố định trên tiêu đề (`Thực tập/Intern`, `Senior`, `Trưởng nhóm/Leader`, `Trưởng phòng/Manager`, `Giám đốc/Director`) ❓Q5; (3) không khớp → `unmapped`.

**Ngành / nghề / tỉnh:** mỗi giá trị nguồn thành 1 dòng `labels` (`unmapped` khi mới gặp). Tỉnh: bảng `province_aliases.csv` quy đổi **tên cũ 63 tỉnh + biến thể** ("HCM", "TP.HCM", "Sài Gòn", "Bình Dương" → "TP. Hồ Chí Minh") → 34 tỉnh mới; nhiều địa điểm → lấy địa điểm đầu.

**Cờ chất lượng:** `reject` theo BR-02 · `warn` khi có cờ (`location_unmapped`, `experience_unparsed`, `salary_invalid`, `salary_ambiguous`, `posted_missing`) · còn lại `ok`.

**Trùng & vòng đời:** cùng nguồn → khoá `(source, source_job_id)`, upsert. Khác nguồn → `dedup_key = md5(norm(công ty)|norm(tiêu đề)|tỉnh)` (norm = chữ thường, bỏ dấu, bỏ "Công ty TNHH/CP", gộp khoảng trắng). `status`: active → expired (qua hạn) → removed (404 hoặc vắng N lần chạy). `event_date = COALESCE(posted_date, ngày first_seen_at)`.

### B5. Dữ liệu (SQLite)

| Bảng | Cột chính | Vì sao có |
|---|---|---|
| `runs` | id, source, scope, started_at, finished_at, status (success/partial/failed), pages, new, updated, rejected, errors, error_sample | Lịch sử chạy; xu hướng chỉ so được khi cùng `scope` |
| `companies` | id, source, name_raw, name_norm | Đếm công ty, dedup (chỉ tên) |
| `job_posts` | id, source, source_job_id (UNIQUE theo source), url, title, company_id, industry/occupation/location/experience/level_label_id, experience_code, level_code, skills_raw, salary_raw, salary_type, salary_from, salary_to, salary_tax, posted_date, deadline, status, description_text, raw_path, raw_sha256, dq_status, dq_flags, dedup_key, first_seen_at, last_seen_at, normalizer_version | Tin đã chuẩn hoá — gồm cả trùng và reject |
| `labels` | id, label_type, source, raw_label, norm_key, status (unmapped/mapped/ignored), master_code, suggestion, first_seen_at · UNIQUE(label_type, source, norm_key) | Ánh xạ; UNIQUE ⇒ nạp lại CSV không tạo dòng trùng; `first_seen_at` = "Phát hiện: <ngày>" trên hàng chờ |
| `label_history` | label_id, at, old, new, reason, via (csv/dashboard) | Nhật ký thay đổi; không có thao tác xoá nhãn |
| `master_*` (industries, occupations, provinces, experiences, levels) | code, name, nhóm cha / min-max tháng / thứ hạng | Danh mục chuẩn riêng, nạp từ `seeds/*.csv` |
| `reports` | period, as_of_date, payload (JSON), xlsx_path, created_at | Báo cáo tháng chốt cứng (BR-10) |

**View `v_jobs_unique`:** `dq_status <> 'reject'` và `ROW_NUMBER() OVER (PARTITION BY dedup_key ORDER BY first_seen_at, id) = 1`; nối `labels` (chỉ khi `mapped`) → mã ngành/nghề/tỉnh/kinh nghiệm/cấp bậc; tính `event_date`, `salary_mid`.

### B6. Dashboard (Streamlit) — theo mockup giao diện low-fi

Khuôn giao diện: [`docs/ui-lowfi.html`](./ui-lowfi.html). Số liệu trong mockup khớp workbook Excel → dùng làm dữ liệu kiểm thử giao diện. Bỏ màn "Việt Nam – Nhật Bản" (ngoài phạm vi) và mọi chỗ "vai trò BA/Admin" (1 người dùng).

**Khung chung (mọi màn):**

| Thành phần mockup | Làm bằng Streamlit |
|---|---|
| Sidebar điều hướng 2 nhóm: *Thị trường* (01 Tổng quan, 02 Nhu cầu, 03 Lương, Xu hướng, Báo cáo tháng) · *Quản trị dữ liệu* (07 Chất lượng & ánh xạ, kèm **số nhãn chưa ánh xạ**) | `st.navigation` có section; tiêu đề trang 07 gắn số đếm |
| Thanh trên: "Dữ liệu chốt: <ngày>" + nút ⓘ mở hộp **Phương pháp & giới hạn dữ liệu** | `st.caption` + `st.dialog` (nội dung: định nghĩa tin duy nhất, flow vs stock, lương đại diện, n<5, thiên lệch giai đoạn đầu) |
| **Bộ lọc dùng chung, giữ nguyên khi chuyển màn**: khoảng thời gian (30 ngày / 90 ngày / 6 tháng / 12 tháng / tuỳ chọn), nguồn, ngành, địa phương, kinh nghiệm, cấp bậc + "Đặt lại bộ lọc" + dòng mô tả bộ lọc đang áp dụng | Widget trong sidebar, lưu `st.session_state` |
| Nút **Xuất CSV** trên mỗi bảng | `st.download_button` (UTF-8 có BOM) |
| Chân trang (BR-09) | 1 hàm `footer()` gọi cuối mọi màn |
| Thông báo sau khi lưu / hộp xác nhận | `st.toast` / `st.dialog` |

**Từng màn:**

| Màn | Nội dung (theo mockup) | Công thức |
|---|---|---|
| **01 Tổng quan** | Huy hiệu **Độ tin cậy** (Cao/Vừa/Thấp) + ngày cập nhật · 6 thẻ chỉ số có giải thích (?) và dòng phụ: tin duy nhất, đang tuyển ("tồn tại ngày chốt"), mới 30 ngày ("±x% so với 30 ngày trước"), số công ty, lương trung vị ("n = … tin có lương số"), % có lương ("a / b tin") · 6 ô biểu đồ: top 5 ngành (thanh ngang, "Còn N ngành khác", có "(Chưa phân loại)"), tin mới theo tháng tách nguồn (cột chồng), top 5 địa phương, lương trung vị theo kinh nghiệm, ô xu hướng (hiện "Chưa đủ dữ liệu" khi chưa đủ 6 tháng), ô chất lượng (phủ ngành, phủ nghề, số tin chưa phân loại) — mỗi ô có link "Xem chi tiết →" | M01, M03–M05, S02, S04, Q02 |
| **02 Nhu cầu** | Dải 5 chỉ số (tổng, đang tuyển, mới 30 ngày, Δ 30 ngày, chưa phân loại) · 4 tab: **Theo ngành** (tổng, tỷ trọng, đang tuyển, mới 30 ngày, 30 ngày trước, Δ%, hạng; dòng "(Chưa phân loại)" và dòng Tổng) · **Theo địa phương** (ghi chú "chỉ lấy địa điểm đầu tiên") · **Theo thời gian** (tháng, tổng mới, từng nguồn, MoM; cảnh báo thiên lệch giai đoạn đầu) · **Bản đồ nhiệt** ngành × địa phương có cột Tổng | M01, M02, M04–M06 |
| **03 Lương** | Hộp lưu ý "chỉ tính range/fixed, không trộn up_to" · 5 tab: **Theo kinh nghiệm / cấp bậc / địa phương / nghề** (số tin, có lương số n, độ phủ, TB, trung vị, P25, P75; nhóm n<5 hiện "n<5") · **Loại lương** (cơ cấu range / fixed / up_to / from / thoả thuận / không ghi trên toàn tập tin duy nhất) · đơn vị triệu VND/tháng | S01–S04, BR-07 |
| **Xu hướng** | Khi < 6 tháng dữ liệu liên tục: màn giải thích điều kiện + tiến độ ("đã có X/6 tháng"). Khi đủ: bảng ngành × 6 tháng, 3T gần nhất, 3T trước, Δ%, nhãn ↑ Tăng / ↓ Giảm / → Ổn định / Chưa đủ dữ liệu. Kỹ năng: Phase sau | T01–T02, BR-08 |
| **Báo cáo tháng** | Danh sách báo cáo đã chốt; xem trước bản nháp tháng chọn; nút **Chốt** (hộp xác nhận) → snapshot + tải Excel | BR-10 |
| **07 Chất lượng & ánh xạ** | Dải chỉ số (tổng bản ghi crawl, tin duy nhất, reject, trùng, chờ ánh xạ) · 3 tab: **Chỉ số chất lượng** (bảng theo nguồn: tổng bản ghi, tin duy nhất, % reject ≤2%, % địa điểm ≥95%, % ngành ≥90%, % nghề ≥80%, % kinh nghiệm ≥90%, Đạt/Không đạt; đếm từng cờ dq; run gần nhất) · **Hàng chờ ánh xạ** (loại nhãn, nguồn, nhãn gốc + "Phát hiện: <ngày>", số tin, gợi ý, hành động *Ánh xạ vào tên chuẩn có sẵn / Bỏ qua / Thêm tên chuẩn mới*, tên chuẩn đích, trạng thái, nút Lưu; lọc theo trạng thái; xuất/nạp CSV) · **Nhật ký thay đổi** (thời điểm, loại nhãn, nguồn, nhãn gốc, cũ → mới, hành động, lý do) | Q01–Q02, B7 |

**Độ tin cậy (mockup Q03, chốt cho bản cá nhân):** Cao nếu n ≥ 30 và mọi chỉ số phủ đạt ngưỡng; Vừa nếu n ≥ 10 hoặc có 1 chỉ số dưới ngưỡng; Thấp còn lại. Ngưỡng nằm trong `config.toml`.

Toàn bộ số liệu lấy từ `stats.py` (dashboard và báo cáo dùng chung 1 chỗ tính). Giao diện dùng dữ liệu mẫu 374 dòng phải ra **đúng các con số in trong mockup** (vd 348 · 64 · 59 · 71 · 14,5 triệu · 57,8%; Δ 30 ngày +7,3%).

### B7. Ánh xạ nhãn bằng CSV
- `python -m jobmarket labels-export` → `data/exports/labels_unmapped_<ngày>.csv` (UTF-8 có BOM): `label_type, source, raw_label, post_count, suggestion, action, master_code, reason`, xếp `post_count` giảm dần. `suggestion` = tên chuẩn gần nhất theo so khớp chuỗi (bỏ dấu, rapidfuzz).
- `python -m jobmarket labels-import <file> [--dry-run]`: `action ∈ {map, ignore}`; dòng lỗi → `<file>.errors.csv`, không dừng cả file; upsert theo khoá UNIQUE; ghi `label_history`.

### B8. Vận hành & chạy lại

| Tình huống | Cách xử lý |
|---|---|
| Máy tắt / chạy chết giữa chừng | Mỗi tin upsert độc lập; lần chạy sau tiếp tục; `runs.status=partial`. Task Scheduler bật "chạy bù khi lỡ lịch" |
| Trang đổi HTML | Test parse trên fixture fail; run có tỷ lệ reject > 2% → dashboard hiện cảnh báo đỏ |
| Sửa quy tắc chuẩn hoá | `python -m jobmarket renormalize --since=YYYY-MM-DD` đọc lại raw, không crawl lại |
| Bị chặn (403/429/captcha) | Backoff, dừng nguồn đó trong lần chạy; không vượt cơ chế chặn |
| Sao lưu | Copy `data/market.db` + `data/raw/` (lệnh `doctor` nhắc nếu > 7 ngày chưa sao lưu) |

**Khối lượng:** ≤ 1 request/2,5 giây ≈ 1.400 request/giờ/nguồn. Trang danh sách chứa nhiều tin; chỉ mở trang chi tiết cho tin **mới** → vài nghìn tin mới/đêm vẫn vừa cửa sổ 01:00–05:00. Muốn crawl **toàn bộ** thị trường thì lần đầu phải chạy rải nhiều đêm ❓Q4.

### B9. Kiểm thử
- Unit (pytest): ≥ 40 chuỗi lương thật, kinh nghiệm, cấp bậc, tỉnh cũ → mới, dedup, cờ chất lượng, xoá PII.
- Parse: fixture HTML/JSON thật (đã xoá PII) cho mỗi nguồn.
- **Khớp mockup:** nạp 374 dòng tab `Data_Jobs` vào DB thử → `stats.py` phải ra đúng số ở tab R1, R2, R4, Quality, Dashboard (vd tin duy nhất 348, đang tuyển 64, lương trung vị 14,5 triệu).
- Nghiệm thu: đối chiếu tay 30 tin/nguồn.

### B10. Lộ trình

| Bước | Nội dung | Xong khi | Ước lượng |
|---|---|---|---|
| 0 Spike | Khảo sát 2 nguồn: endpoint/HTML, trường có sẵn, cần JS không, robots, điều khoản | Ghi chú spike + quyết định Playwright | 1–2 ngày · **đã làm 2026-11-03 → [`spike-0.md`](./spike-0.md)**, chờ quyết định D1/D2 |
| 1 Nền | Khung project, SQLite + migrations + seeds, xoá PII, raw store, 2 adapter, chuẩn hoá, cờ, dedup, CLI `run` | ≥ 300 tin/nguồn; chỉ số chất lượng đạt | 1,5–2 tuần · **code xong 2026-11-03**; crawl tự động TopCV bị chặn → thay bằng `import-html` (file người dùng tự lưu) + `import-csv`; chưa đạt "≥ 300 tin/nguồn" |
| 2 Thống kê | `stats.py` + test khớp mockup | Ra đúng số trên dữ liệu `Data_Jobs` | 3–4 ngày · **xong 2026-11-03** — khớp toàn bộ số Dashboard/R1/R2/R4/Quality (`tests/test_stats_mockup.py`) |
| 3 Dashboard | 6 màn Streamlit + bộ lọc + CSV + chân trang | Dùng hằng ngày được | 4–5 ngày · **xong 2026-11-03** (5 màn; "Báo cáo tháng" thuộc Bước 4) — `tests/test_dashboard.py` chạy từng màn bằng AppTest, số Tổng quan khớp mockup |
| 4 Nhãn & báo cáo | CSV xuất/nạp, sửa nhãn trên dashboard, chốt báo cáo tháng → Excel | Nạp lại không trùng; báo cáo chốt không đổi | 3 ngày · **xong 2026-11-03** (sửa nhãn trên dashboard + `remap`; báo cáo tháng khớp tab R5, chốt cố định, Excel 6 sheet; xuất/nạp nhãn bằng CSV chưa làm — dashboard thay thế) |
| 5 Tự động hoá | Task Scheduler, `doctor`, README | Chạy 7 đêm liên tiếp không cần can thiệp | 1–2 ngày · **có script + lệnh đăng ký** (`scripts/run_nightly.ps1`, README); chờ chạy thật 7 đêm |
| Sau | Kỹ năng (từ điển), PDF báo cáo, thêm nguồn | — | — |

### B11. Câu hỏi cần bạn chốt

| # | Câu hỏi | Đề xuất |
|---|---|---|
| Q1 | Tên + vị trí folder | `D:\Project\ccareer-project\jobmarket-crawl\`, git repo riêng |
| Q2 | Chạy ở đâu: máy cá nhân (phải bật lúc 02:00) hay VPS? | Máy cá nhân trước; chuyển VPS nếu hay lỡ lịch |
| Q3 | Dashboard Streamlit có ổn, hay chỉ cần xuất Excel theo mockup? | Streamlit (Excel vẫn có qua báo cáo tháng) |
| Q4 | Phạm vi crawl: toàn bộ tin, hay giới hạn ngành/tỉnh/từ khoá? | Giới hạn theo ngành bạn quan tâm trước, mở rộng sau |
| Q5 | Cho phép suy cấp bậc từ tiêu đề theo bảng từ khoá cố định? | Có |
| Q6 | Danh mục ngành/nghề: dùng nguyên 12 ngành + ~30 nghề trong tab Master? | Có, bổ sung dần qua ánh xạ nhãn |
| Q7 | Tỷ giá USD và hệ số quy đổi lương tuần/giờ | 25.500 (theo tab Params), tuần ×4,33, giờ ×8×26 — chỉnh trong config |
| Q8 | Địa phương: mockup dùng **tên tỉnh cũ** (Bình Dương, Hồ Chí Minh…), plan quy về **34 tỉnh mới** (Bình Dương gộp vào TP. Hồ Chí Minh) — số theo tỉnh sẽ khác mockup | 34 tỉnh mới (khớp dữ liệu hành chính hiện hành); test "khớp mockup" bỏ qua bảng theo địa phương. Nếu muốn so được với dữ liệu cũ → lưu cả `province_old` để xem theo tên cũ |

---

### Bàn giao
**Trạng thái:** SẴN SÀNG BẮT ĐẦU — phạm vi đã chốt "chỉ dùng cá nhân"; Q1–Q7 dùng giá trị đề xuất
**Bằng chứng chính:** `docs/ycau-goc.md`; Excel tab README, Formulas, Params, Phases, Master, Quality, Data_Jobs
**Việc cho agent khác:** Sau khi chốt Q1–Q7: tạo agent riêng `jobmarket-dev` (dự án không thuộc CCareer — các agent `ccareer-*` không áp dụng) → Spike (Bước 0)
**Cần user quyết:** Q1–Q7
**File đã tạo/sửa:** `docs/PLAN.md` (v2; chuyển từ `task/task24-plan.md` ngày 2026-11-03; v1 ở `_trash/task24-plan.v1-tich-hop-ccareer.md` của monorepo)
