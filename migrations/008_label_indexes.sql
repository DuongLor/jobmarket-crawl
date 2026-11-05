-- Màn "Chất lượng" (đếm số tin mỗi nhãn: labels LEFT JOIN job_posts ON p.<loại>_label_id = l.id) quét lại toàn bảng
-- job_posts cho TỪNG nhãn → ~24 giây trên 41k tin (đo 2026-11-05). Có index → < 0,1 giây.
-- Index status: DATA_VERSION_SQL của dashboard đếm tin hết hạn mỗi lần render trang.
CREATE INDEX IF NOT EXISTS idx_job_posts_industry_label   ON job_posts(industry_label_id);
CREATE INDEX IF NOT EXISTS idx_job_posts_occupation_label ON job_posts(occupation_label_id);
CREATE INDEX IF NOT EXISTS idx_job_posts_location_label   ON job_posts(location_label_id);
CREATE INDEX IF NOT EXISTS idx_job_posts_experience_label ON job_posts(experience_label_id);
CREATE INDEX IF NOT EXISTS idx_job_posts_level_label      ON job_posts(level_label_id);
CREATE INDEX IF NOT EXISTS idx_job_posts_status           ON job_posts(status, source);
