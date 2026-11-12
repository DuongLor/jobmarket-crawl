-- 2026-11-09: phát hiện tin qua sitemap (kênh site công bố cho máy tìm kiếm) — Việc Làm 24h chỉ cho đọc trang 1 của
-- mỗi danh sách (trang 2 trả 403) nên phần lớn tin chỉ thấy được qua sitemap + trang chi tiết.

-- Mỗi URL tin có trong sitemap của nguồn. `checked_at` = đã đọc trang chi tiết (kết quả ở `outcome`) → không đọc lại.
-- Tin đã có trong job_posts (thấy ở trang danh sách) thì không cần đọc trang chi tiết.
CREATE TABLE sitemap_jobs (
    source          TEXT NOT NULL,
    source_job_id   TEXT NOT NULL,
    url             TEXT NOT NULL,
    first_listed_at TEXT NOT NULL,
    last_listed_at  TEXT NOT NULL,
    checked_at      TEXT,
    outcome         TEXT,  -- saved | gone (404/410) | no_data
    PRIMARY KEY (source, source_job_id)
);

CREATE INDEX idx_sitemap_jobs_pending ON sitemap_jobs (source, checked_at);

-- Lần tải sitemap gần nhất mỗi nguồn — sitemap cập nhật hằng ngày, không tải lại mỗi lần chạy
CREATE TABLE sitemap_fetches (
    source     TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    files      INTEGER NOT NULL,
    urls       INTEGER NOT NULL
);
