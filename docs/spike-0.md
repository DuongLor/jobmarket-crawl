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

## Lượt 6 — rà soát độ đúng của dữ liệu crawl (2026-11-04)

- **Đối chiếu DB ↔ thẻ tin đã lưu** (`scripts/audit_careerlink.py`, không request mạng): 3.457 tin, 12 trường
  (mã, URL, tiêu đề, công ty, lương gốc/loại/khoảng, địa điểm/số địa điểm, cấp bậc, ngày) → **0 lệch**. Thẻ tin chỉ có
  6 trường dữ liệu (tiêu đề, công ty, địa điểm, ngày cập nhật, lương, cấp bậc) và đều đã đọc.
- **Ngày trên thẻ tin là ngày CẬP NHẬT**, không phải ngày đăng gốc: 9/9 tin có trang chi tiết có `datePosted` sớm hơn
  (vd 12/10 so với 04/11). Trang chi tiết có thì dùng `datePosted`.
- **`industry` trang chi tiết là DANH SÁCH mục** cách nhau dấu phẩy, mục đầu = chức năng chính → tách, lấy mục chính
  (bỏ "Khác"), không thay ngành chuẩn đang có bằng nhãn chưa ánh xạ. Trước đó cả chuỗi thành 1 nhãn chưa ánh xạ và
  ghi đè ngành đúng từ trang danh mục (13/15 tin đã sửa lại bằng `backfill`, từ JSON-LD đã lưu).
- **Lương thực tập/part-time 1–3 triệu là thật** (CareerLink 8, VietJobs 554 tin bị loại oan) → ngưỡng tối thiểu 3 triệu
  → 1 triệu (chỉ bắt lỗi đơn vị như "0.1 triệu"); `renormalize` tính lại từ chuỗi gốc. Trung vị thực tập CareerLink
  4,5 → 3,8 triệu.
- **Độ phủ**: danh sách chung 688 trang (~34.000 tin) nhưng mỗi lần chạy chỉ đọc ≤10 trang đầu → **quét sâu**
  (`resume_page`, 8 trang/lần, lùi 2 trang chống sót). Hết hạn đổi sang **theo vòng quét trọn** (một vòng mất nhiều
  ngày; luật "14 ngày không thấy" sẽ đánh dấu nhầm tin còn tuyển); tin thấy lại trên danh sách → còn tuyển.
- Lần chạy thật 04/11 (code quét sâu): 34 trang danh sách + 15 trang chi tiết, không bị hộp xác minh; +591 tin mới,
  kinh nghiệm/hạn nộp/ngành chi tiết đọc đúng.

## Lượt 7 — quét toàn site CareerLink, TopCV (2026-11-04)

- **TopCV**: `robots.txt` đọc được (không cấm trang tin), nhưng `sitemap.xml` cũng bị Cloudflare chặn ("Attention
  Required", 403) với User-Agent tự khai → không có kênh tự động nào mở. **Không vượt** (không giả trình duyệt, không
  dùng trình duyệt tự động giải challenge, không điều khiển trình duyệt của người dùng). Cách lấy đủ trường: người dùng
  tự lưu trang khi duyệt → `import-html` nay đọc cả **trang chi tiết** (JSON-LD `JobPosting`, `sources/jsonld.py` dùng
  chung với CareerLink). Mẫu test `tests/fixtures/topcv/detail_synthetic.html` là mẫu tự dựng theo schema.org — cần đối
  chiếu khi có trang TopCV thật.
- **PII**: SĐT ở cuối câu ("… 0912 345 678.") trước đây không bị xoá (biên phải cấm mọi dấu chấm) → sửa biên; quét lại
  DB + 3.472 file data/raw: 0 chỗ còn PII.
- **CareerLink toàn site**: 25 trang sâu/lần (≤ 50 request/lần như cũ) → vòng quét ~5 ngày với lịch 6 lần/ngày. Danh mục
  thấy trên trang chi tiết được nhớ (`crawl_listings`) và duyệt ở lần sau → gắn ngành cho tin ngoài 8 danh mục trang chủ.

## Lượt 8 — thêm nguồn + rà soát đa agent (2026-11-04)

- **Việc Làm 24h** (`sources/vieclam24h.py`): robots chỉ cấm `/admin`, `/taikhoan`, `/*?q`; UA tự khai nhận 200. Dữ
  liệu có cấu trúc trong `__NEXT_DATA__` (30 tin/trang + bảng tra tên ngành/nghề/tỉnh/kinh nghiệm/cấp bậc) → đủ 100%
  trường. **Trang 2 (`?page=2`) trả 403** dù robots không cấm → không thử vượt; mỗi lần đọc trang 1 của trang tìm nhanh
  + 53 trang nghề. Ngành = nghề chính `occupation_ids_main[0]` (chức năng), không phải lĩnh vực công ty.
- **CareerViet** (`sources/careerviet.py`): robots cấm `/vi/jobs/*` (trang chi tiết) → chỉ thẻ tin danh sách; 50
  tin/trang, `pageCount` (370 trang ≈ 18.500 tin) trong payload Next.js. Thẻ có 1–3 mục ngành (`ul.welfare`), hạn nộp,
  ngày CẬP NHẬT (không có ngày đăng gốc), không có kinh nghiệm/cấp bậc. Lần đầu: 35 trang, 1.747 tin, không bị chặn.
- **Bỏ qua**: 123job (tổng hợp lại tin nơi khác, tên người ở ô công ty, ngày tương đối, không ngành/hạn nộp), TopDev
  (từ chối kết nối SSL). Chưa làm: ITviec (chỉ IT), timviec365 (chưa thấy trang danh sách).
- **Rà soát 5 agent** (pm, tester, risk-reviewer, dev, docs-keeper) → đã sửa:
  - PII: email nhân viên tuyển dụng lồng trong `employer_info.assigned_staff_username` (Việc Làm 24h) lọt vào 30 file
    bản gốc + fixture → bản gốc đổi sang danh sách trắng; lớp chặn chung `store/raw.sanitize` (bỏ key liên hệ ở mọi độ
    sâu + scrub mọi chuỗi) cho mọi nguồn; `scripts/scrub_raw.py` làm sạch file đã lưu (kiểm lại 2026-11-04: 0/7.502 file còn email).
  - Vòng quét: trang trống khi không biết trang cuối bị coi là "quét trọn" → đánh hết hạn nhầm hàng loạt (mô phỏng
    được). Nay chỉ chốt vòng khi đã đọc qua trang cuối của thanh phân trang/`pageCount`.
  - robots.txt: `urllib.robotparser` không hiểu `*`/`$` → `fetch/robots.py` theo RFC 9309; chuyển hướng tự theo và
    kiểm robots từng bước; 429 dừng ngay.
  - Thống kê: "Đang tuyển" 100% (chưa vòng quét trọn nào) → chỉ đếm tin xác định được trạng thái; ngày đăng/làm mới
    trước ngày bắt đầu thu thập không tính vào tháng/MoM/xu hướng (bảng tháng từng hiện +5.095%); tháng bắt đầu giữa
    chừng không tính là tháng trọn; cơ cấu ngành chỉ tin có ngành theo tin (CareerLink suy từ 4 danh mục đã duyệt bị
    loại khỏi tỷ trọng) và đếm chia đều tin nhiều ngành (bảng `job_industry_labels`, migration 007).
  - Hạn nộp gia hạn được cập nhật; tin `active` không còn thấy cũng hết hạn; mã tin dùng làm tên file được kiểm (chặn
    `../`); `start` không sập khi bước tự nạp lỗi; `run` bỏ qua nguồn đã chạy < 3,5 giờ (`--force` để ép).
- **Còn mở**: nghề (occupation) 0% mọi nguồn; kinh nghiệm CareerLink/CareerViet gần 0% (chỉ có ở trang chi tiết —
  CareerLink giới hạn, CareerViet robots cấm); CareerLink vẫn hay bị challenge ngay trang 2 dù cách 4 giờ.

## Lượt 9 — rà soát lại bằng 3 agent (2026-11-05)

- **Hiệu năng**: màn Chất lượng 28–36 s → ~4,5 s (lần đầu, gồm tải dữ liệu). Nguyên nhân: đếm số tin mỗi nhãn quét lại
  toàn bảng `job_posts` cho từng nhãn → migration 008 thêm index các cột `*_label_id` + `(status, source)`; thẻ "Nhãn
  chờ ánh xạ" chỉ đếm `COUNT(*)`; dòng "(Tất cả)" gộp từ bảng theo nguồn thay vì đọc lại `v_jobs_unique`.
- **Đúng/sai**: file TopCV lưu tay cũ không làm sống lại tin đã hết hạn, trang chi tiết cũ không ghi đè hạn nộp mới;
  "đã quét trọn" chỉ tính danh sách gốc (`start_url`), không phải danh mục nhỏ bất kỳ; MoM để trống ở tháng có nguồn
  bắt đầu thu thập giữa chừng (từ ngày 4) và tháng kế tiếp; số đếm chia đều làm tròn giữ đúng tổng (0,4+0,3+0,3 = 1).
- **Tầng request**: robots.txt có BOM UTF-8 trước đây bị bỏ qua toàn bộ; robots.txt trả 429 = cấm hết; Retry-After âm/
  NaN/ngày giờ không làm sập; 503 ở lần thử cuối = bị chặn; chuyển hướng sang giao thức khác http(s) thì dừng.
- **Dữ liệu cá nhân**: thêm tổng đài 1900/1800 và key `hotline`/`fax`.
- **Quét sâu**: phần đầu danh sách trước đây dừng sau 3 trang "không có tin mới" → tin cũ vừa được làm mới (nhảy từ
  dưới con trỏ quét sâu lên đầu) không được thấy lại trong vòng → bị đánh hết hạn nhầm cuối vòng. Nay trang chỉ là
  "đã biết" khi MỌI tin đã thấy trong vòng quét hiện tại. Đổi lại: đầu mỗi vòng mới (~5 ngày/lần) phần đầu đọc đủ
  `max_pages` (10 trang) thay vì ~3–4; vẫn trong `max_requests`.
- **CSV** thiếu `first_seen_at`: lấy ngày đăng làm ngày thấy (trước đây = giờ nạp → mọi tin thành tin tồn sẵn).
- **Lương**: lương đại diện > 150 triệu/tháng (`[stats] max_monthly_salary`; vd "500 triệu", thường nhập sai hoặc lương
  năm) không vào thống kê lương — 11 tin trên DB thật (khoảng rộng đã bị `max_range_ratio` loại phần lớn); TB lương bộ
  lịch sử 15,42 → 15,30 triệu. `raw.save` trả mã băm của file trên đĩa khi file đã có (trước đây của bản mới không ghi).
- **Không sửa (có lý do)**: mã tin CareerViet viết hoa là số hex (`35B09EDD`) → khác hoa/thường vẫn là cùng tin, không
  có va chạm thật; `dq_status` cũ sau khi ánh xạ lại chỉ ảnh hưởng dòng `status` của CLI (thống kê lọc `reject`, bảng
  cờ dashboard tính lại theo nhãn hiện tại).
- **Ánh xạ ngành CSKH**: "Dịch vụ khách hàng"/"Chăm sóc khách hàng" (184 tin: tổng đài, telesales, nhắc phí, QHKH ngân
  hàng; rất ít lễ tân/nhà hàng) IND10 → **IND03 Kinh doanh – Bán hàng**, khớp `seeds/occupations.csv` (OCC09 thuộc
  IND03). "Thực phẩm – Đồ uống" giữ IND02 (2 tin, đều sản xuất/R&D). `remap` nay áp lại cả nhãn do quy tắc ánh xạ
  (`mapped_via = 'rule'`), không đụng nhãn sửa tay. Đã chạy trên DB thật: 3 nhãn; tỷ trọng Kinh doanh 14,4% → 16,9%,
  Dịch vụ – NH – KS 4,7% → 2,0%.
- **Nghề — quyết định không suy từ tiêu đề**: danh mục nghề chuẩn chỉ là bộ mẫu 27 nghề (2–3/ngành); suy theo từ khoá
  sẽ để phần lớn tin "chưa phân loại" và tỷ trọng chỉ phản ánh các nghề trong danh sách mẫu. Dashboard đã ẩn chiều
  nghề khi độ phủ thấp; màn Chất lượng không chấm tiêu chí mà chưa nguồn nào cung cấp (trước đây mọi nguồn "Chưa đạt:
  occupation", che lỗi thật). Làm nghề cần trước hết một danh mục nghề đầy đủ.
- **Còn mở**: tin trên con trỏ quét sâu hết hạn hàng loạt (> `deep_overlap_pages` trang giữa 2 lần chạy) vẫn có thể làm
  tin dịch lên vùng đã đọc → hết hạn nhầm, tự hồi phục khi thấy lại ở vòng sau.

## Lượt 9 — ngành CareerLink theo danh mục (2026-11-09, không request mạng)

- **Sửa nhận định Lượt 6:** `industry` trang chi tiết xếp mục theo **MÃ danh mục tăng dần** (8/8 tin nhiều mục, vd
  CNTT 19 → Bán hàng 31 → Tư vấn kỹ thuật 205), **không** phải "mục đầu = chức năng chính". Lấy mục đầu làm ngành
  chính khiến mã nhỏ luôn thắng ("Nhân viên kinh doanh" → Dịch vụ khách hàng 9; "Dược sĩ bán thuốc" → Tư vấn DVKH 7).
  Nguồn không có mục chính → thống kê **chia đều** tin cho mọi mục; `industry_label_id` chỉ là nhãn đại diện để lọc.
- **Hiện trạng đo trên DB:** 4.916 tin, 54% không ngành; ngành còn lại là danh mục đang duyệt lúc gặp tin; chỉ 25 tin có
  trang chi tiết → tỷ trọng ngành gần như không có tin CareerLink. 18 danh mục đã biết (2 cặp trùng mã: `ke-toan/1` =
  `ke-toan-kiem-toan/1`, `cntt-phan-mem/19` = `it-phan-mem/19`), hầu hết chưa từng quét: danh sách chung luôn đi đầu
  và dùng hết ngân sách ~40 request/lần.
- **Thay đổi:** tin gặp ở danh mục nào thì **tích luỹ** danh mục đó (`job_industry_labels`); `category_first = true`
  (danh sách chung vào vòng xoay, bỏ trang tỉnh, danh mục cùng mã là một); ngành theo danh mục vào tỷ trọng khi
  **mọi** danh mục ngành đã quét trọn ≥ 1 vòng (`health` hiện tiến độ). `backfill` nạp lại từ thẻ tin đã lưu:
  1.953 tin có mục ngành (trước: 25 tính được).
- **Địa điểm đúng:** 4.886/4.916 ánh xạ, theo 34 tỉnh mới (vd tin "… - Hải Dương" hiện Hải Phòng là đúng sau sáp nhập).
  Chưa xử lý: 26 tin làm việc tại Nhật Bản đang "bỏ qua" (ngoài danh mục tỉnh), 287 tin nhiều nơi chỉ tính nơi đầu.

## Lượt 10 — phân loại giống CareerLink (2026-11-09, migration 009, không request mạng)

- **Ngành 2 cấp:** `master_categories` (danh mục đặt tên đúng như CareerLink, kèm mã `/viec-lam/<slug>/<id>` nếu biết)
  thuộc một nhóm trong 15 nhóm `master_industries`. `labels.category_code` = danh mục của nhãn ngành nguồn. 23 danh mục
  đã biết nạp từ `seeds/categories.csv`; danh mục CareerLink mới gặp **tự thêm** (tên CareerLink, nhóm theo ánh xạ).
  Nguồn khác chỉ có danh mục khi tương đương rõ (`seeds/category_aliases.csv`); VietJobs (nhóm rộng) chỉ có nhóm.
  Đo trên DB: 1.953 tin CareerLink, 4.041 CareerViet, 530 Việc Làm 24h có danh mục.
- **Mọi nơi làm việc:** `job_locations` (+ `job_posts.location_raw`), nạp lại từ bản gốc bằng `backfill-locations`
  (thẻ tin; Việc Làm 24h: mã tỉnh trong `item`, tên học từ tin 1 tỉnh; VietJobs: file dataset). 3.205 tin nhiều nơi
  (CareerLink 287, CareerViet 582, Việc Làm 24h 135, VietJobs 2.201). Thống kê theo tỉnh đếm tin ở **mỗi** nơi (như
  CareerLink lọc theo tỉnh → tổng các tỉnh > số tin); lọc tỉnh khớp nếu bất kỳ nơi nào khớp.
- **Tên tỉnh** theo CareerLink: "Hồ Chí Minh" (mã HCM giữ nguyên).
- View cho bên đọc ngoài: `v_job_categories`, `v_job_locations` (MySQL chỉ chép bảng, không chép view).

## Lượt 11 — quy tắc thu thập 3 mức, sitemap, nghỉ sau khi bị chặn (2026-11-09, migration 010)

UA trung thực, ~30 request tổng (robots 8 site, sitemap, 1 trang chi tiết/site, chạy thử 13 request trên bản sao DB).

| Nguồn | Phát hiện | Xử lý |
|---|---|---|
| CareerLink | Bị chặn 14:44/15:02/15:29 ngày 09/11 vì chạy cách nhau 15–30 phút bằng `--force`/`--now` (chốt 3,5 giờ bị bỏ qua) — lỗi nhịp của ta, không phải chặn hẳn | `blocked_cooldown_hours` (6, ×2 mỗi lần chặn liền, trần 24); `--force`/`--now` không bỏ qua được. Đang nghỉ tới 15:29 10/11 (3 lần liền) |
| Việc Làm 24h | robots khai `Sitemap` + `Crawl-delay: 5`; sitemap tin 16.646 URL (DB có 920 tin, 15.804 chưa thấy); trang chi tiết 200, có `item` cùng cấu trúc trang danh sách + JSON-LD | Tầng 3 `discover_from_sitemap`: tin chưa thấy → đọc trang chi tiết, mã lớn trước, `sitemap_max_per_run = 100`, sitemap tải lại mỗi 24 giờ. Thử thật 5 tin: đủ trường, dq `ok`, bản gốc không có liên hệ |
| JobsGO | `sitemap-job.xml` 23.299 URL (lastmod từng tin); trang chi tiết: curl 200 có JSON-LD, **httpx 403** cùng UA | Giữ tắt — 403 riêng với client Python = tín hiệu chặn bot; đổi client để qua thuộc mức Vàng, cần user duyệt |
| TopCV | `sitemap.xml` 403 (Cloudflare) | Userscript `scripts/topcv-save.user.js` lưu trang user tự mở → Downloads → `start`/`watch`/`import-html` gom vào inbox. Chưa thử với trang TopCV thật (fixture vẫn là mẫu tự dựng) |
| VietnamWorks | robots 404, sitemap 404 | Không đổi |
| ITviec, vieclam.gov.vn | robots cho phép (ITviec có sitemap; vieclam.gov.vn `Crawl-delay: 1`) | Ứng viên nguồn mới, chưa làm |

- `Crawl-delay` của robots nay được tôn trọng (max với `min_delay_seconds`); > 120 giây = coi như cấm.
- Lưu ý dữ liệu: `created_at` của Việc Làm 24h có thể rất cũ (tin dùng lại từ 2018) trong khi JSON-LD `datePosted` là
  ngày đăng lại — giữ `created_at` cho cả 2 đường (danh sách, sitemap) để cùng một tin có cùng ngày đăng; chưa đổi.

## Lượt 12 — thêm nguồn ITviec (2026-11-10)

UA trung thực, ~10 request khảo sát + 5 request chạy thử trên bản sao DB.

- robots: `User-agent: *` chỉ cấm `/subscriptions/new`, `Allow: /`, sitemap `dunggiatminh.xml` (613 URL trang chi tiết).
  Danh sách `/it-jobs` (cả `?page=2`) và trang chi tiết đều 200, không challenge (chữ "captcha" trong trang là script
  reCAPTCHA của form, không phải trang chặn). Cả site ~619 tin IT, 20 tin/trang, 31 trang → quét trọn mỗi lần chạy.
- Thẻ tin: mã `data-job-key` (UUID), tiêu đề, công ty, nghề, tỉnh (tiếng Anh không dấu; nhiều tỉnh "A - B" → tách trong
  adapter), kỹ năng, "Posted N … ago" (phút/giờ/ngày → ngày; tuần/tháng → để trống). Trang chi tiết JSON-LD: ngày đăng,
  hạn nộp, ngành "Information Technology" (alias → IND01, không gán danh mục vì gồm cả phần mềm/phần cứng), mô tả.
- **Không lấy:** lương (ẩn, cần đăng nhập — "Sign in to view salary", JSON-LD "You'll love it"); cấp bậc (không có);
  kinh nghiệm JSON-LD **không đáng tin** (tin ghi "ít nhất 3 năm" mà `monthsOfExperience` = 10; tin khác 37) → bỏ.
- Nghề: chỉ 4 tên tương đương rõ được ánh xạ (`seeds/label_aliases.csv`: Backend/Frontend Developer, SDET, Business
  Analyst); còn lại `unmapped` (danh mục nghề chuẩn chỉ có 4 nghề IT) → chỉ số phủ nghề của ITviec sẽ thấp.
- Chạy thử thật: 20 tin, 3 trang chi tiết, 20/20 dq `ok`, ngành IND01, tỉnh HCM/HN/DN đúng, 0 lỗi.

## Lượt 13 — mô tả, yêu cầu công việc, kinh nghiệm theo tháng (2026-11-11, migration 011)

- Cột mới `job_posts`: `requirements_text`, `experience_raw` (chữ nguồn ghi), `experience_min_months`,
  `experience_max_months` ("1 - 2 năm" → 12/24; "1 năm" → 12/NULL = tối thiểu; "Dưới 1 năm" → 0/12; "Chưa có kinh
  nghiệm" → 0/0; chữ không rõ → NULL, không đoán). Nhóm 6 mức (`experience_label_id`) giữ nguyên cho thống kê.
  Lỗi tránh được: `norm_key` xoá "-" → "1 - 2 năm" thành "1 2 nam" (đọc ra 24 tháng) → bộ đọc chỉ bỏ dấu, giữ ký hiệu.
- Nguồn: Việc Làm 24h — trang chi tiết `item.description_html` + `job_requirement`/`skill_requirement`/
  `other_requirement` (bật `detail_max_per_run`; tin từ sitemap ghi luôn khi lưu). CareerLink — mô tả JSON-LD tách tại
  dòng "* Kinh nghiệm / Kỹ năng chi tiết:" (13/13 mô tả thật tách đúng), kinh nghiệm `monthsOfExperience`. ITviec —
  mục `div.paragraph > h2` "Mô tả công việc"/"Job description", "Yêu cầu công việc"/"Your skills and experience"
  (kiểm trang thật tiếng Anh); kinh nghiệm vẫn bỏ. CareerViet — robots cấm trang chi tiết → không có.
- `backfill-details` nạp cho tin cũ: 35.334 tin có khoảng tháng từ nhãn đã lưu; 13 mô tả CareerLink tách yêu cầu.
- Dashboard "Danh sách tin": chi tiết tin hiện kinh nghiệm (chữ + khoảng), mô tả, yêu cầu công việc.
- Lần chạy 11/11 08:36–09:27 (code trước lượt này): CareerLink +1.011 · Việc Làm 24h +226 (sitemap 100) · CareerViet
  +1.228 · ITviec +619 (trọn site) — không nguồn nào chặn.
- 2026-11-11 16:19: lần `run --full` Việc Làm 24h dừng sau 179/14.879 trang vì **1 ReadTimeout** ở trang chi tiết (+1 lỗi
  502 ở trang nghề o27) — lỗi phía máy chủ, không phải chặn. Sửa: client thử lại timeout/mất kết nối/502/504 (nghỉ lâu
  dần); hết lượt thì bỏ qua đúng tin đó (lần sau đọc lại) và đi tiếp; chỉ dừng tầng khi 5 trang LIỀN lỗi. Không tính là
  bị chặn → không kích hoạt thời gian nghỉ.
