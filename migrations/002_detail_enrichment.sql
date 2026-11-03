-- Tầng 2 của crawl: bổ sung từ trang chi tiết (JSON-LD JobPosting) — ngành, kinh nghiệm, ngày đăng, hạn nộp, mô tả.
-- detail_fetched_at NULL = chưa bổ sung; bộ chạy ưu tiên tin mới nhất, mỗi lần chạy giới hạn số trang chi tiết.
ALTER TABLE job_posts ADD COLUMN detail_fetched_at TEXT;
ALTER TABLE job_posts ADD COLUMN employment_type TEXT;
CREATE INDEX idx_job_posts_detail_pending ON job_posts(source, detail_fetched_at);
