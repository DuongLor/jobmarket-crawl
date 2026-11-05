# Plan v3 — Đáp ứng `task/ycau.md` (phạm vi dùng cá nhân)

> **Trạng thái:** đề xuất · 2026-11-05 · chưa code. Bổ sung cho [`PLAN.md`](./PLAN.md) (v2); khi lệch nhau thì plan này
> thay cho các mục cùng chủ đề trong v2.
> **Phạm vi đã chốt:** vẫn **dùng cá nhân** (1 người, máy cá nhân, không chia sẻ/công khai dữ liệu). Các mục trong
> `ycau.md` gắn với hệ thống công ty (Laravel, nhiều vai trò người xem) được giữ ở dạng "sẵn sàng" chứ không làm.
> **Yêu cầu gốc:** [`../../task/ycau.md`](../../task/ycau.md) — mốc 1A 20/11 · 1B 26/11 · 1C 30/11.

---

## 1. Hiện trạng so với yêu cầu (đo trên DB thật 2026-11-05)

| Yêu cầu | Hiện trạng | Đánh giá |
|---|---|---|
| Crawler Python chạy độc lập | `jobmarket` CLI + `start` | ✅ |
| DB riêng, không trộn DB vận hành | SQLite `data/market.db` | ✅ |
| Laravel chỉ đọc DB để hiển thị | Không có Laravel; hiển thị bằng Streamlit (127.0.0.1) | ➖ ngoài phạm vi cá nhân (mục 3.7) |
| Taxonomy ngành / địa phương | 15 ngành, 34 tỉnh sau sáp nhập, bảng bí danh + ánh xạ tay | ✅ |
| Taxonomy kỹ năng | Không có. 3 nguồn crawl: 0% tin có kỹ năng, không có mô tả (trừ 25 tin CareerLink đọc chi tiết). VietJobs (lịch sử) có danh sách kỹ năng ở 29.913 tin | ❌ |
| Nhu cầu theo ngành/địa phương/thời gian | Màn Nhu cầu; "tăng/giảm" cần ≥ 6 tháng (đang 0 tháng trọn) | ⚠️ |
| Lương theo vị trí + kinh nghiệm | Kinh nghiệm: Việc Làm 24h 100%, CareerLink 0,5%, CareerViet 0%. Vị trí (nghề): 0% | ⚠️ |
| So sánh Việt Nam – Nhật | Không có nguồn Nhật | ❌ |
| Xu hướng + kỹ năng nổi lên | Không có dữ liệu kỹ năng; xu hướng cần nhiều tháng | ❌ |
| Báo cáo định kỳ tuần/tháng | Báo cáo tháng (Excel, chốt cố định); chưa có tuần | ⚠️ |
| Ghi nguồn + thời gian thu thập | Chân trang mọi màn | ✅ |
| Thoả thuận không vào trung bình, tách tỉ lệ không ghi lương | BR-06/BR-07 | ✅ |
| Chỉ dùng số liệu tổng hợp theo ngày **đã kiểm chứng** | Tính trực tiếp từ view, chưa có bảng tổng hợp theo ngày + bước kiểm chứng | ❌ |
| Chưa phân loại ghi rõ | Nhóm "(chưa phân loại)" | ✅ |
| Tuân thủ nguồn: giãn tần suất, dừng khi bị chặn | robots RFC 9309, 10 s/request, ≥ 4 giờ/lần, dừng khi chặn | ✅ |
| 1A: dữ liệu thật ≥ 7 ngày liên tục | CareerLink 3 ngày (3 lần lỗi, 5 lần một phần), CareerViet/Việc Làm 24h 2 ngày; `start` mở tay | ⚠️ |
| 1B: kiểm tra tay ngẫu nhiên khớp | Chỉ có `scripts/audit_careerlink.py` (so DB với bản gốc, không so với trang thật) | ⚠️ |
| 1C: dashboard + Việt Nam–Nhật + 1 báo cáo mẫu | Dashboard có; Việt Nam–Nhật không; báo cáo tháng 11 phải chờ hết tháng | ⚠️ |

**Lỗi phát hiện khi đo (ảnh hưởng 1A):** Ctrl+C lúc đang crawl để lại lần chạy ở trạng thái `running` mãi
(Việc Làm 24h 10:56 05/11), và `_crawl_due` vẫn tính lần đó là "đã chạy" → nguồn bị hoãn 4 giờ dù không có dữ liệu.

---

## 2. Quyết định thiết kế

| # | Vấn đề | Chọn | Lý do |
|---|---|---|---|
| D1 | Laravel | **Không làm** (cá nhân). Giữ DB "sẵn sàng cho bên đọc": mọi số liệu đi qua view, có tài liệu hợp đồng dữ liệu | Thêm Laravel cho 1 người dùng là thừa; khi thành dự án công ty chỉ cần trỏ Laravel vào view |
| D2 | Nguồn Nhật | **Số liệu công khai của chính phủ Nhật qua e-Stat API** — "Tình hình giới thiệu việc làm" của Bộ Y tế–Lao động (一般職業紹介状況: số chỗ tuyển mới, tỷ lệ chỗ tuyển/người tìm việc theo nhóm nghề và tỉnh, theo tháng). Lương: "Điều tra cơ cấu tiền lương" (賃金構造基本統計調査, theo năm) | Hợp lệ, có API, có giấy phép dùng lại; không crawl trang tuyển dụng Nhật (điều khoản + chống bot) |
| D3 | So sánh "cùng nhóm ngành" | Bảng `seeds/vn_jp_groups.csv`: ~8 **nhóm so sánh chung**, mỗi nhóm = danh sách mã ngành Việt Nam + danh sách nhóm nghề Nhật | Ngành Việt Nam (theo tin đăng) và nghề Nhật (theo phân loại nghề) không khớp 1–1; so ở nhóm chung đủ thô để đúng |
| D4 | So sánh "cùng kỳ" | Chỉ so tháng mà **cả hai bên** có số liệu trọn. Nhật công bố tháng N vào cuối tháng N+1 | Không so lệch kỳ (quy tắc §3 của yêu cầu) |
| D5 | So sánh cái gì | **Cơ cấu** (tỷ trọng nhóm trong tổng) và **xu hướng** (chỉ số so với kỳ gốc); **không** so số tuyệt đối. Lương: chỉ đặt cạnh nhau, ghi rõ khác định nghĩa | Việt Nam = số tin đăng online; Nhật = số chỗ tuyển qua cơ quan việc làm công → khác đơn vị, khác phạm vi |
| D6 | Kỳ so sánh có ngay | **07–10/2025**: VietJobs (lịch sử, cơ cấu ngành) ↔ Nhật cùng 4 tháng (cộng dồn) — chỉ cơ cấu (VietJobs không có ngày đăng từng tin) | Đến 30/11 dữ liệu crawl chưa có tháng trọn nào; đây là cặp cùng kỳ duy nhất |
| D7 | Kỹ năng | **Từ điển kỹ năng cố định** dựng từ danh sách kỹ năng VietJobs (tần suất cao) + bổ sung tay; trích bằng khớp từ/cụm (không AI) từ tiêu đề + mô tả + thẻ kỹ năng nếu nguồn có | Không AI (quy ước chung); VietJobs cho sẵn từ vựng thực tế |
| D8 | Nguồn kỹ năng cho dữ liệu crawl | Spike trước (mục 3.4): đo độ phủ khi chỉ có tiêu đề. Đạt ≥ 30% tin → dùng; không đạt → xu hướng kỹ năng chỉ trên tin có mô tả, ghi rõ độ phủ | Tiêu đề thường không nêu kỹ năng; không đoán để "đủ số" |
| D9 | "Số liệu theo ngày đã kiểm chứng" | Bảng `daily_snapshots`: sau mỗi ngày, chốt số liệu tổng hợp theo ngày × nguồn × ngành × tỉnh **chỉ khi qua cổng kiểm tra**; báo cáo tuần/tháng đọc snapshot đã kiểm chứng | Đúng chữ yêu cầu; số cũ không đổi khi ánh xạ lại (như báo cáo tháng) |
| D10 | Chạy định kỳ | Task Scheduler gọi `run` mỗi 4 giờ (**cần bạn duyệt** — thay đổi hệ thống), hoặc giữ `start` chạy liên tục. Chọn **một** | `start` phụ thuộc cửa sổ đang mở; Task Scheduler chạy kể cả khi quên mở |

---

## 3. Việc cần làm

Mỗi việc: đầu ra kiểm được, test tự động, cổng chất lượng `ruff` + `pytest` xanh.

### 3.1 [1A] Ổn định thu thập — làm ngay (05–08/11)
> **Tiến độ 05/11:** xong sửa lần chạy bỏ dở (`jobmarket/health.py::close_stale_runs`, trạng thái `interrupted`, đếm lại
> tin từ DB; `_crawl_due` bỏ qua lần bị ngắt chưa lưu tin) + lệnh `health`. Lần #18 (Việc Làm 24h 10:56) đã đóng:
> 132 tin mới, 381 cập nhật. Chuỗi ≥ 2 nguồn: 2/7 (04–05/11). **Còn: chọn D10.**
- **Sửa lần chạy bỏ dở:** bắt `KeyboardInterrupt`/thoát ngang trong `run_paged`/`start` → đóng lần chạy với trạng thái
  `interrupted`; khi khởi động, đóng các lần `running` cũ (> 2 giờ). `_crawl_due` không tính lần chạy `interrupted` /
  không có trang nào đọc được.
- **Lệnh `jobmarket health`:** mỗi nguồn: số ngày liên tục có dữ liệu (≥ 1 lần chạy success/partial có trang đọc
  được trong ngày), lần chạy gần nhất, tỷ lệ lỗi 24 giờ, lần chạy kẹt. Định nghĩa "7 ngày liên tục" = **mỗi ngày,
  mỗi nguồn trong ≥ 2 nguồn** có ít nhất 1 lần chạy có dữ liệu.
- **Chọn D10** và bật lịch chạy.
- Nghiệm thu: `health` hiện chuỗi ngày ≥ 7 cho ≥ 2 nguồn. **Phải chạy ổn định liên tục từ ≤ 13/11** để kịp 20/11.

### 3.2 [1B] Kiểm tra tay ngẫu nhiên (06–12/11)
> **Tiến độ 05/11:** xong lệnh `audit` (`jobmarket/audit.py`): chọn mẫu theo seed từ tin chưa hết hạn, thấy trong
> 3 ngày qua; mỗi dòng = 1 tin × 1 trường (gốc → chuẩn hoá) + ô chọn Đúng/Sai/Bỏ qua; `--score` ghi
> `docs/audit-log.md`. Lượt 1: `data/audit/audit_2026-11-05_1446_seed325649.xlsx` (90 tin, 540 ô) — **chờ bạn đánh
> dấu**. Còn: kiểm số tổng hợp (cần snapshot §3.3).
- **Lệnh `jobmarket audit --sample 30 [--source]`:** chọn ngẫu nhiên (có seed, ghi lại) 30 tin/nguồn → file
  `data/audit/<ngày>.xlsx`: link tin, các trường đã chuẩn hoá (ngành, tỉnh, lương gốc → khoảng số, kinh nghiệm, ngày
  đăng, hạn nộp) + cột "Đúng/Sai/Ghi chú" để bạn mở trang thật và đánh dấu.
- Lệnh `audit --score <file>`: đọc file đã đánh dấu → % trường đúng theo nguồn × trường, ghi vào `docs/audit-log.md`.
- Thêm một lượt kiểm **số tổng hợp**: chọn ngẫu nhiên 5 ô (ngành × tỉnh × ngày) trong snapshot, in câu SQL đếm lại
  từ tin gốc để đối chiếu.
- Nghiệm thu: ≥ 95% trường đúng mỗi nguồn (thước đo A1 của PLAN v2); trường dưới ngưỡng → sửa bộ đọc/ánh xạ rồi kiểm lại.

### 3.3 [1B] Số liệu theo ngày đã kiểm chứng (08–14/11)
- Migration `009_daily_snapshots.sql`: `daily_snapshots(day, source, industry_code, province_code, new_jobs,
  active_jobs, salary_n, salary_median, verified_at, checks_json)`.
- Lệnh `jobmarket snapshot [--day]` (tự gọi sau lần chạy cuối mỗi ngày): tính → chạy cổng kiểm tra → ghi.
  Cổng: lần chạy của ngày không `failed`; reject ≤ 2%; số tin mới không lệch > 3× trung vị 7 ngày trước (lệch → đánh
  dấu "cần xem", không tự chốt); tổng các ô = tổng tin.
- Báo cáo tuần/tháng và màn so sánh đọc snapshot `verified`; màn hiện ngày nào chưa kiểm chứng.

### 3.4 [1B] Kỹ năng (spike 06–08/11, làm 10–20/11 nếu đạt)
- **Spike:** dựng từ điển nháp từ 300 kỹ năng hay gặp nhất của VietJobs (gộp biến thể: "MS Excel" = "Excel") → đo %
  tin crawl (chỉ có tiêu đề) khớp ≥ 1 kỹ năng. Ghi kết quả vào `docs/spike-0.md`.
- Nếu đạt D8: `seeds/skills.csv` (mã, tên chuẩn, nhóm, bí danh), bảng `job_skills(job_id, skill_code, source_field)`,
  trích khi lưu tin + `backfill`; nhãn kỹ năng chưa biết **không** tự thêm (khác ngành: kỹ năng trích theo từ điển).
- Màn Xu hướng thêm "Kỹ năng": top kỹ năng theo kỳ, Δ so kỳ trước (cùng ngưỡng BR-08), luôn ghi độ phủ kỹ năng.
- Không đạt: báo cáo kỹ năng chỉ trên bộ lịch sử VietJobs (cơ cấu, không có xu hướng) + ghi rõ lý do.

### 3.5 [1C] Báo cáo tuần (12–20/11)
- `report` nhận kỳ `YYYY-Www` (tuần ISO, thứ Hai–Chủ nhật) ngoài `YYYY-MM`; chỉ số giống báo cáo tháng, so với tuần
  trước; đọc snapshot đã kiểm chứng; chốt cố định như báo cáo tháng.
- Báo cáo mẫu: **tuần 47 (17–23/11/2026)** — tuần trọn đầu tiên sau khi đủ 7 ngày liên tục.

### 3.6 [1C] So sánh Việt Nam – Nhật (14–28/11)
- **Bạn làm:** đăng ký appId e-Stat (miễn phí) → đặt vào `.env` (`ESTAT_APP_ID`), không commit.
- Spike (14–16/11): xác định đúng bảng thống kê + mã nhóm nghề + mã tháng trên e-Stat; tải thử 07–10/2025.
- `jobmarket import-jp --from 2025-07 --to 2025-10`: lưu bảng `jp_stats(month, jp_group, prefecture, new_openings,
  openings_ratio, source_table, fetched_at)`; lương Nhật theo năm vào `jp_wages`. Tỷ giá JPY→VND trong config (như USD).
- `seeds/vn_jp_groups.csv` (D3) — **bạn duyệt** danh sách nhóm trước khi dùng.
- Màn "Việt Nam – Nhật": chọn kỳ (chỉ kỳ cả hai bên có), bảng + biểu đồ cơ cấu theo nhóm chung; xu hướng chỉ số khi có
  ≥ 2 tháng chung; lương đặt cạnh nhau. Ghi chú bắt buộc: định nghĩa từng bên, nguồn, ngày cập nhật, nhóm không so được.
- Nghiệm thu: số Nhật trên màn khớp bảng gốc e-Stat (kiểm tay 5 ô); kỳ so sánh hai bên giống hệt.

### 3.7 Sẵn sàng cho bên đọc ngoài (Laravel sau này) — nhỏ (20–26/11)
- `docs/data-contract.md`: các view (`v_jobs_unique`, snapshot, jp_stats) + ý nghĩa cột + quy tắc (chỉ đọc, chỉ snapshot
  verified cho báo cáo). Không thêm code Laravel.

### 3.8 [1C] Bản demo (26–30/11)
- Danh sách màn cần demo + báo cáo mẫu tuần 47 + báo cáo Việt Nam–Nhật 07–10/2025, xuất Excel.
- Ghi rõ giới hạn: tháng 11 chưa trọn, xu hướng ngành cần ≥ 6 tháng, kinh nghiệm/nghề thiếu ở 2 nguồn.

---

## 4. Lịch so với mốc

| Ngày | Việc | Mốc |
|---|---|---|
| 05–08/11 | 3.1 ổn định + health + lịch chạy · spike kỹ năng | Bắt đầu chuỗi ngày liên tục ≤ 08/11 |
| 06–14/11 | 3.2 audit · 3.3 snapshot | |
| 14–16/11 | Spike e-Stat (cần appId) | |
| 10–20/11 | 3.4 kỹ năng (nếu đạt) · 3.5 báo cáo tuần | **20/11 — 1A:** ≥ 7 ngày liên tục |
| 16–26/11 | 3.6 Việt Nam–Nhật · 3.7 hợp đồng dữ liệu · kiểm tay lần 2 | **26/11 — 1B:** kiểm tay ≥ 95% |
| 24–30/11 | Báo cáo mẫu tuần 47 · 3.8 demo | **30/11 — 1C** |

Đường găng: 3.1 (mỗi ngày trễ = trễ chuỗi 7 ngày) và appId e-Stat (3.6 không bắt đầu được nếu thiếu).

---

## 5. Rủi ro

| Rủi ro | Ảnh hưởng | Xử lý |
|---|---|---|
| CareerLink bị hạn chế ngầm nhiều lần chạy | Chuỗi ngày của CareerLink đứt | Định nghĩa 7 ngày theo ≥ 2 nguồn; CareerViet + Việc Làm 24h ổn định |
| Bảng e-Stat khác dự kiến (mã nhóm nghề, độ trễ công bố) | Trễ 3.6 | Spike sớm 14–16/11; kỳ 07–10/2025 đã công bố từ lâu |
| Kỹ năng từ tiêu đề quá thấp | Không có xu hướng kỹ năng cho dữ liệu crawl | Theo D8: báo cáo kỹ năng trên bộ lịch sử, ghi rõ |
| So sánh Việt Nam–Nhật bị hiểu là so số tuyệt đối | Kết luận sai | D5: chỉ cơ cấu + chỉ số; ghi chú định nghĩa trên màn |
| Điều khoản nguồn | — | Giữ phạm vi cá nhân; nếu chuyển thành dự án công ty phải xem lại (PLAN v2 A4) |

---

## 6. Cần bạn
1. **Duyệt D10:** Task Scheduler (mỗi 4 giờ) hay giữ `start` mở liên tục.
2. **Đăng ký appId e-Stat** trước 14/11.
3. **Duyệt `vn_jp_groups.csv`** khi có bản nháp (≈ 16/11).
4. **Đánh dấu file audit** (30 tin/nguồn, ~30–45 phút/lượt) — 2 lượt: ≈ 12/11 và ≈ 24/11.
