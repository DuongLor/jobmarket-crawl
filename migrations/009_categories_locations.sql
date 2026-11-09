-- 2026-11-09: phân loại giống CareerLink.
-- 1) Ngành 2 cấp: DANH MỤC (kiểu CareerLink, ~72 mục phẳng) thuộc một NHÓM (master_industries, 15 nhóm để so sánh
--    giữa các nguồn). Nhãn ngành nguồn (labels) giữ master_code = nhóm; category_code = danh mục khi tương đương rõ.
-- 2) Tin nhiều nơi làm việc: lưu ĐỦ mọi nơi (job_locations), không chỉ nơi đầu; job_posts.location_raw giữ chuỗi gốc.
-- 3) Tên tỉnh hiển thị theo CareerLink ("Hồ Chí Minh"); mã giữ nguyên nên số liệu không đổi.

CREATE TABLE master_categories (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,                        -- tên đúng như CareerLink hiển thị
    industry_code TEXT REFERENCES master_industries (code),   -- nhóm cha; NULL = chưa xếp nhóm
    careerlink_id INTEGER,                                     -- mã danh mục CareerLink (/viec-lam/<slug>/<id>)
    sort_order    INTEGER NOT NULL
);

-- Tên khác của cùng một danh mục (CareerLink đổi tên, nguồn khác gọi khác) — quy tắc, sửa seed rồi `remap`
CREATE TABLE category_aliases (
    alias_norm    TEXT PRIMARY KEY,
    category_code TEXT NOT NULL REFERENCES master_categories (code)
);

ALTER TABLE labels ADD COLUMN category_code TEXT REFERENCES master_categories (code);

ALTER TABLE job_posts ADD COLUMN location_raw TEXT;

CREATE TABLE job_locations (
    job_id   INTEGER NOT NULL REFERENCES job_posts (id),
    position INTEGER NOT NULL,
    label_id INTEGER NOT NULL REFERENCES labels (id),
    PRIMARY KEY (job_id, position)
);
CREATE INDEX idx_job_locations_label ON job_locations (label_id);

-- Bên đọc ngoài (MySQL/Workbench, Laravel sau này): danh mục và mọi nơi làm việc của từng tin
CREATE VIEW v_job_categories AS
SELECT j.job_id, j.position, l.raw_label AS source_label, c.code AS category_code, c.name AS category_name,
       COALESCE(c.industry_code, CASE WHEN l.status = 'mapped' THEN l.master_code END) AS industry_code
FROM job_industry_labels j
JOIN labels l ON l.id = j.label_id
LEFT JOIN master_categories c ON c.code = l.category_code
WHERE l.status <> 'ignored';

CREATE VIEW v_job_locations AS
SELECT jl.job_id, jl.position, l.raw_label AS source_label, p.code AS province_code, p.name AS province_name
FROM job_locations jl
JOIN labels l ON l.id = jl.label_id
LEFT JOIN master_provinces p ON p.code = l.master_code AND l.status = 'mapped';

UPDATE master_provinces SET name = 'Hồ Chí Minh' WHERE code = 'HCM';
