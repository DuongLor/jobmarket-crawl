# Bước 0 — Khảo sát 2 nguồn (spike)

> 2026-11-03 · Tổng cộng ~12 request (robots, 1 trang danh sách + 1 trang chi tiết mỗi nguồn, sitemap, 2 file JS) · cách nhau ≥ 2–3 giây · User-Agent trình duyệt thường · không đăng nhập.
> File tải về chỉ để phân tích, nằm ở thư mục tạm của phiên Claude Code, **không** đưa vào repo (chưa xoá PII).

## Kết luận nhanh

| | TopCV | VietnamWorks |
|---|---|---|
| `robots.txt` | Có; chỉ cấm khu vực CV/private (`/cv-ung-vien/`, `/xem-cv/`, `/private/`…). Trang việc làm **không bị cấm** | **Không có** (404) → không khai báo hạn chế |
| Trang danh sách | ✅ HTML render sẵn, không bị chặn. "Tuyển dụng 41.600 việc làm" · ~45 tin/trang · phân trang `?page=N` · có URL theo ngành (`/tim-viec-lam-<ngành>-cr<id>`) và theo tỉnh (`…-tai-<tỉnh>-l<id>`) | ⚠️ HTML không chứa danh sách tin — trình duyệt tải sau qua API. Có sẵn **toàn bộ danh mục** của VNW trong `__NEXT_DATA__` |
| Trang chi tiết | ❌ **Bị Cloudflare chặn** ("Just a moment… Enable JavaScript and cookies", HTTP 403, managed challenge) | ✅ HTML render sẵn, dữ liệu có cấu trúc đầy đủ trong payload React Server Components |
| Sitemap | ❌ `/sitemap.xml` cũng 403 | ❌ `/sitemap.xml` 404 |
| JSON-LD `JobPosting` | Không kiểm được (chi tiết bị chặn) | Không có |

## TopCV

**Thẻ tin trên trang danh sách có:** mã tin (`data-job-id`), tiêu đề, URL chi tiết, tên công ty + URL công ty, lương dạng chữ ("Từ 60 triệu", "Tới 20 triệu", "Thoả thuận"…), địa điểm, kinh nghiệm ("Không yêu cầu"…), tag (kỹ năng/ngành con, không ổn định).

**Không có trên danh sách:** ngành chuẩn, cấp bậc, ngày đăng, hạn nộp, mô tả, số lượng tuyển.

**Địa điểm đã theo danh mục hành chính mới** — hiển thị "Hồ Chí Minh (mới)", tooltip "Địa điểm làm việc đã được cập nhật theo Danh mục Hành chính mới".

**Trang chi tiết bị Cloudflare managed challenge.** Theo quy tắc dự án (PLAN, agent `jobmarket-dev`): **không vượt** lớp chặn này (không dùng trình duyệt tự động để giải challenge, không dùng công cụ né Cloudflare). Đây là tín hiệu rõ TopCV không muốn truy cập tự động vào trang chi tiết.

**Cách lấy dữ liệu khả thi chỉ bằng trang danh sách:**
- Duyệt danh sách **theo từng ngành** (`-cr<id>`) → ngành của tin = ngành của trang đang duyệt (một tin có thể xuất hiện ở nhiều ngành → lấy ngành đầu tiên gặp, ghi nhận nhãn).
- Ngày đăng → dùng `first_seen_at` (PLAN đã có `event_date = COALESCE(posted_date, first_seen_at)`).
- Hạn nộp / trạng thái → tin biến mất khỏi danh sách N lần chạy liên tiếp = `removed`/hết hạn.
- Cấp bậc → chỉ suy được từ tiêu đề theo bảng từ khoá (Q5), phần lớn sẽ `unmapped`.
- Khối lượng: 41.600 tin ÷ 45 ≈ 925 trang; ở 1 request/2,5 giây ≈ 40 phút cho toàn bộ — vừa cửa sổ đêm.
- ⚠️ Chưa kiểm: trang danh sách sâu (`?page=2+`) và trang theo ngành có bị challenge không khi duyệt liên tục — Cloudflare có thể bật challenge khi thấy nhiều request.

## VietnamWorks

**Trang chi tiết** (`…-<jobId>-jd`) có object tin đầy đủ trong payload RSC (`self.__next_f.push`), các trường đã thấy:
`jobId`, `jobTitle`, `companyName`, `salaryMin`, `salaryMax`, `salaryCurrency`, `salaryPeriodId`, `prettySalary` / `prettySalaryVI` ("Thương lượng"), `jobLevel` / `jobLevelVI` ("Trưởng phòng"), `industriesV3`, `jobFunction`, `workingLocations`, `address`, `approvedOn` (ngày đăng), `expiredOn` (hạn nộp), `yearsOfExperience` (**số năm, dạng số**), `skills`, `benefits`, `numberOfRecruits`, `highestDegreeId`.
Có cả `contactName`, `contactEmail` (tin mẫu để trống) → **phải xoá trước khi lưu**. Một số trường là tham chiếu (`"$29"`) → cần parser payload RSC (dòng `29:{…}`).

**Trang danh sách**: tin không có trong HTML; trình duyệt gọi API tìm kiếm riêng của VNW. Lần thử gọi API đó (POST, có gắn `Origin`/`Referer` của trang) **bị chặn bởi bộ kiểm soát an toàn của Claude Code** với lý do "vượt rào truy cập website" — chưa chạy, không thử lại.

**Danh mục có sẵn trong `__NEXT_DATA__`** (dùng làm bảng nhãn nguồn, không cần crawl thêm):
- Cấp bậc (5): Thực tập sinh/Sinh viên · Mới tốt nghiệp · Nhân viên · Trưởng phòng · Giám đốc và cấp cao hơn → ánh xạ vào 7 cấp của dự án (thiếu Senior/Trưởng nhóm).
- Địa điểm (66): **63 tỉnh cũ** + "Quốc tế", "ĐBSCL", "Khác" → khác TopCV (đã dùng tên mới).
- Ngành/chức năng: 72 category, 30 nhóm chức năng cha (+ con), 49 ngành công ty.
- Lương theo khoảng **USD** (≤ $500 … ≥ $3000) → quy đổi USD quan trọng với nguồn này.

## Ảnh hưởng tới PLAN

| Mục PLAN | Thay đổi đề xuất |
|---|---|
| Q8 tỉnh | Xác nhận: 2 nguồn dùng 2 hệ khác nhau (TopCV tên mới, VNW 63 tên cũ) → bảng `province_aliases.csv` phải phủ cả hai → quy về 34 tỉnh mới |
| B4 cấp bậc | VNW có trường cấp bậc (5 mức); TopCV chỉ có tiêu đề |
| B4 kinh nghiệm | VNW cho số năm → map thẳng vào 6 nhóm; TopCV dạng chữ |
| B1 Playwright | **Không dùng** — với TopCV nó chỉ có ích để giải challenge, điều dự án không làm |
| A2 trường dữ liệu | TopCV thiếu: ngành (bù bằng trang theo ngành), cấp bậc, ngày đăng, hạn nộp, mô tả |

## Lượt khảo sát 2 (2026-11-03, 3 request)

| Thử | Kết quả |
|---|---|
| VNW trang tìm kiếm HTML `viec-lam?q=ke-toan` | 200, nhưng chỉ có 5 link tin "nổi bật" lặp lại — **kết quả tìm kiếm không có trong HTML** → phương án D2(b) không khả thi |
| TopCV `tim-viec-lam-moi-nhat?page=2` | ❌ **403 Cloudflare challenge** — chỉ trang 1 của danh sách vào được |
| TopCV trang ngành `tim-viec-lam-cong-nghe-thong-tin-cr257` (trang 1) | ✅ 200, 50 tin, tổng "2.616 việc làm CNTT" |

**Kết luận:** TopCV chỉ cho xem trang 1 (≈ 50 tin) của mỗi danh sách; phân trang và trang chi tiết đều bị chặn. VNW không có danh sách tin dạng HTML.

Việc ghép nhiều trang 1 (theo ngành, theo tỉnh…) để dựng lại toàn bộ dữ liệu về kỹ thuật không phá lớp chặn nào, nhưng **đi ngược ý định** của TopCV khi khoá phân trang. Nếu dùng, chỉ nên coi là **mẫu hằng ngày** (vài chục URL/ngày), ghi rõ trên dashboard là dữ liệu TopCV không đầy đủ, không dùng để kết luận tổng số tin.

## Lượt 3 — chạy thật pipeline (2026-11-03)

| Thử | Kết quả |
|---|---|
| `python -m jobmarket run` với User-Agent trung thực `jobmarket-crawl/0.1 (personal, non-commercial, low-rate)` | ❌ **TopCV chặn ngay trang đầu** (403 Cloudflare challenge). Client dừng sau 2 request đúng thiết kế, run #1 = `failed` |
| So với lượt 1 | Lượt 1 vào được trang danh sách vì dùng User-Agent trình duyệt → Cloudflare chặn **bot tự khai danh tính**. Đổi sang UA giả trình duyệt = nguỵ trang để né chống bot → **không làm** |
| `vieclam.gov.vn/robots.txt` (Sàn GDVL Quốc gia) | ✅ `Allow: /`, `Allow: /search/`, `Allow: /search/job-detail/`, `Crawl-delay: 1`; chỉ cấm trang đăng ký/đăng nhập. UA trung thực **không bị chặn** |
| `vieclam.gov.vn/` và `/search/` | 200 nhưng là SPA (vỏ HTML 3,5KB) — dữ liệu tải qua JS/API |
| Tải file JS của vieclam.gov.vn để tìm endpoint dữ liệu | ⛔ Bộ kiểm soát an toàn của Claude Code chặn (không nêu lý do) — chưa chạy |

**Kết luận lượt 3:** TopCV không dùng được cho thu thập tự động hợp lệ (chặn bot tự khai danh tính). Sàn GDVL Quốc gia là nguồn duy nhất đến giờ **robots cho phép rõ ràng**, nhưng cần tìm endpoint dữ liệu của SPA — việc này cần user cho phép.

## Lượt 4 — tìm nguồn cho phép bot tự khai danh tính (2026-11-03)

UA trung thực `jobmarket-crawl/0.1 (personal, non-commercial, low-rate)`, mỗi site 1 request robots + 1 trang danh sách.

| Nguồn | robots.txt | Trang danh sách | Ghi chú |
|---|---|---|---|
| **CareerLink** | Cho phép (chỉ cấm khu vực tài khoản) | ✅ 200, ~50 tin/trang, ~33.900 tin | **Đang dùng, tốc độ thấp.** Có cấp bậc. Có trang theo ngành `/viec-lam/<ngành>/<id>` và tỉnh `/tim-viec-lam-tai/…` |
| JobsGO | Cho phép (cấm /ajax, /api) | curl: 200, 53 tin/trang, ~26.000 tin, có trang theo ngành | ❌ Lần chạy thật (httpx) bị **403 challenge** → **tắt**, không chỉnh header để né |
| ITviec | `Allow: /` | ✅ 200, ~23 tin (chỉ IT) | Chưa làm adapter |
| Việc Làm 24h | Cho phép trang danh sách (cấm `/*?q`) | ✅ 200, dữ liệu trong `__NEXT_DATA__` | Chưa làm adapter |
| Timviec365 | `Allow: /` | ✅ 200 trang chủ, ~77 link tin | Chưa làm adapter |
| CareerViet | Không trả nội dung | — | Bỏ qua |

Kết quả chạy thật CareerLink: 2 trang → 99 tin; danh sách chung 22 trang → 945 tin mới trong 56 giây.

**Đính chính (2026-11-03, sau lần chạy thứ 3):** ban đầu tưởng "trang 23 trống" là giới hạn phân trang. Sai — vài phút
sau **chính trang 1 cũng trả trang không có tin** (HTTP 200, tiêu đề "CareerLink"). Tức CareerLink **hạn chế ngầm** client
sau ~25 request trong < 1 phút. Xử lý (không lách): `min_delay_seconds = 10`, `max_requests = 60`/đêm, pipeline phát hiện
"trang danh sách không có tin" là dừng nguồn. Tổng đã thu: 1.044 tin. Chưa biết ngưỡng chính xác; theo dõi các đêm sau ở
màn Chất lượng (bảng "Các lần chạy gần nhất"). Các nguồn còn lại có thể bổ sung dần theo cùng khuôn adapter (`listings`, `page_url`, `parse_listing`, tuỳ chọn `discover`).

## Quyết định cần user chọn

**D1 — TopCV:**
- (a) **Chỉ dùng trang danh sách** (bỏ chi tiết), chấp nhận thiếu cấp bậc/hạn nộp/mô tả, ngày đăng = ngày thấy lần đầu. *Đề xuất.*
- (b) Bỏ TopCV, chỉ dùng VietnamWorks.

**D2 — Cách lấy danh sách tin VietnamWorks:**
- (a) Dùng API tìm kiếm mà chính website VNW gọi (không cần đăng nhập). Cần user **đồng ý rõ ràng** và có thể phải thêm quyền cho lệnh này trong cài đặt Claude Code, vì bộ kiểm soát đã chặn lần thử. Rủi ro: API không công khai, có thể đổi bất cứ lúc nào.
- (b) Khảo sát thêm xem trang tìm kiếm theo ngành/tỉnh của VNW (`/viec-lam-<…>`) có render sẵn danh sách tin không, chỉ dùng HTML công khai. Tốn thêm vài request khảo sát.
- (c) Chỉ crawl chi tiết các tin VNW lấy được link từ nơi khác — không khả thi để theo dõi thị trường.

~~Đề xuất: thử (b) trước~~ — (b) đã thử, không khả thi (lượt 2). Còn (a) hoặc (d).
- (d) Bổ sung/thay bằng nguồn **cho phép truy cập tự động rõ ràng** (có API công khai, RSS, hoặc robots + điều khoản cho phép) — cần khảo sát danh sách nguồn thay thế.

**D1 cập nhật:** (a) giờ nghĩa là "trang 1 của các trang ngành, làm mẫu hằng ngày" (không phải toàn bộ 41.600 tin).

## Đề xuất chốt (2026-11-03)

Mục đích dự án là **tham khảo, theo dõi** → cái cần là **cơ cấu tương đối** (ngành nào nhiều, lương theo nhóm, xu hướng tăng/giảm), không cần tổng số tin tuyệt đối. Một mẫu ổn định, cùng cách lấy mỗi ngày, là đủ cho mục đích này.

| Vai trò | Nguồn | Cách lấy | Trạng thái |
|---|---|---|---|
| **Nguồn chính, làm ngay** | TopCV — trang 1 của mỗi trang ngành (~50 trang/ngày, ~2.000–2.500 tin mới nhất) | HTML công khai, robots cho phép, không chạm trang chi tiết/phân trang | Khả thi, đã xác minh |
| **Nguồn mở rộng, thử song song** | Jooble REST API (tổng hợp tin từ nhiều trang) | API chính thức, đăng ký key miễn phí tại jooble.org/api/about; POST `keywords` + `location`, trả `title, location, company, salary, link, updated` | Chưa xác minh độ phủ Việt Nam — cần key để thử |
| **Dữ liệu nền để kiểm thử pipeline** (tuỳ chọn) | Bộ dữ liệu VietJobs (48.092 tin, 34 tỉnh, giấy phép CC BY 4.0, arXiv 2603.05262) | Tải 1 lần, nhập như nguồn riêng `vietjobs` | Không trộn vào xu hướng hằng ngày |
| **Bỏ** | VietnamWorks qua API nội bộ | — | Không công khai, đã bị chặn khi thử |
| Xem sau | Sàn việc làm quốc gia `vieclam.gov.vn` (Bộ Nội vụ) | Chưa rõ có API/dữ liệu mở | Khảo sát khi có thời gian |

Hệ quả cho PLAN: mỗi nguồn là 1 adapter (`html_listing`, `api`, `dataset_import`); dashboard ghi rõ "TopCV: mẫu trang 1 theo ngành"; trường thiếu của TopCV (cấp bậc, hạn nộp, mô tả) để trống + cờ, không suy đoán.

## Lượt 5 — độ đầy đủ của CareerLink (2026-11-03 chiều)

- **Ngày đăng có sẵn trên thẻ tin**: `<time datetime="…Z">` (nhãn "Cập nhật", UTC) — khớp `datePosted` của trang chi
  tiết (mẫu 3665468). Trước đây bị bỏ qua → 2.866/2.866 tin đã điền lại từ thẻ tin lưu trong `data/raw`
  (`python -m jobmarket backfill`, không request mạng). Lưu ý: tin đăng lại được làm mới ngày.
- **Ngành**: thẻ tin không có. Chỉ lấy được từ (a) trang danh mục `/viec-lam/<slug>/<id>` — trang chủ chỉ liên kết
  8 danh mục, `/so-do-trang-web` 5, trang "Xem tất cả lĩnh vực" (`/tim-viec-lam-nhanh`) bị hộp xác minh; hoặc
  (b) JSON-LD `industry` trang chi tiết (vd "Khách sạn"), ánh xạ qua `seeds/industry_aliases.csv`.
  "Tiếng Nhật" là danh mục kỹ năng → bỏ qua, tin nhận ngành thật khi gặp ở danh mục khác.
- **Thử suy ngành từ tiêu đề** (từ khoá cố định): đúng 63–71% khi đối chiếu ~35.500 tin có ngành do nguồn gắn
  (nguồn phân theo lĩnh vực, từ khoá bắt theo chức năng) → **không dùng**, không đủ chuẩn.
- **Kinh nghiệm**: chỉ có ở trang chi tiết.
- **Trang chi tiết bị hộp "Xác nhận bạn không phải robot"** (HTTP 200) sau ~20 request chiều 03/11; sau đó cả
  `/tim-viec-lam-nhanh` cũng vậy. Theo nguyên tắc dự án: **dừng, không vượt**. `PoliteClient` nay nhận diện hộp
  này (`CHALLENGE_TEXT_200`) → `Blocked`; trang chi tiết thiếu JSON-LD không còn bị đánh dấu "đã đọc" ngay
  (3 trang liền → coi là bị hạn chế, dừng). Lịch chạy tự thử lại ở lần sau.
- **Xoay vòng danh sách** (`crawl_listings`): mỗi lần chạy ưu tiên danh mục chưa duyệt/duyệt lâu nhất, trang gắn
  được ngành cho tin cũ cũng tính là "có ích" → các lần chạy sau phủ dần mọi danh mục thay vì lặp lại 4 danh mục đầu.
