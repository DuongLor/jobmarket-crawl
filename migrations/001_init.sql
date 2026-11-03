-- Schema ban đầu. Đổi schema = thêm file migrations/NNN_*.sql mới, không sửa file này.

CREATE TABLE runs (
    id              INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,
    scope           TEXT NOT NULL,              -- JSON: danh sách URL/phạm vi đã duyệt; xu hướng chỉ so được khi cùng scope
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running', -- running | success | partial | failed
    pages_fetched   INTEGER NOT NULL DEFAULT 0,
    posts_new       INTEGER NOT NULL DEFAULT 0,
    posts_updated   INTEGER NOT NULL DEFAULT 0,
    posts_rejected  INTEGER NOT NULL DEFAULT 0,
    errors_count    INTEGER NOT NULL DEFAULT 0,
    error_sample    TEXT,                       -- JSON: tối đa vài lỗi để gỡ rối
    app_version     TEXT NOT NULL
);

CREATE TABLE companies (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    name_raw    TEXT NOT NULL,
    name_norm   TEXT NOT NULL,                  -- chỉ tên; không lưu thông tin liên hệ
    UNIQUE (source, name_norm)
);

-- Danh mục chuẩn của dự án (nạp từ seeds/*.csv)
CREATE TABLE master_industries  (code TEXT PRIMARY KEY, name TEXT NOT NULL, sort_order INTEGER NOT NULL);
CREATE TABLE master_occupations (code TEXT PRIMARY KEY, name TEXT NOT NULL, industry_code TEXT NOT NULL REFERENCES master_industries(code));
CREATE TABLE master_provinces   (code TEXT PRIMARY KEY, name TEXT NOT NULL, name_en TEXT NOT NULL);
CREATE TABLE master_experiences (code TEXT PRIMARY KEY, name TEXT NOT NULL, min_months INTEGER NOT NULL, max_months INTEGER, sort_order INTEGER NOT NULL);
CREATE TABLE master_levels      (code TEXT PRIMARY KEY, name TEXT NOT NULL, rank_order INTEGER NOT NULL);

-- Tên tỉnh cũ (63) + biến thể chính tả → 34 tỉnh mới. Khoá là chuỗi đã chuẩn hoá (text.norm_key).
CREATE TABLE province_aliases (
    alias_norm     TEXT PRIMARY KEY,
    province_code  TEXT NOT NULL REFERENCES master_provinces(code)
);

-- Mỗi giá trị nguồn (ngành/nghề/tỉnh/kinh nghiệm/cấp bậc) là 1 nhãn.
-- Tin trỏ tới nhãn; view nối nhãn → mã chuẩn lúc truy vấn ⇒ ánh xạ đổi là số liệu đổi ngay (BR-10).
CREATE TABLE labels (
    id             INTEGER PRIMARY KEY,
    label_type     TEXT NOT NULL,               -- industry | occupation | location | experience | level
    source         TEXT NOT NULL,
    raw_label      TEXT NOT NULL,
    norm_key       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'unmapped', -- unmapped | mapped | ignored
    master_code    TEXT,
    mapped_via     TEXT,                        -- rule | csv | dashboard
    suggestion     TEXT,
    first_seen_at  TEXT NOT NULL,
    UNIQUE (label_type, source, norm_key)
);

-- Nhật ký thay đổi nhãn; chỉ thêm, không sửa/xoá.
CREATE TABLE label_history (
    id         INTEGER PRIMARY KEY,
    label_id   INTEGER NOT NULL REFERENCES labels(id),
    at         TEXT NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    reason     TEXT,
    via        TEXT NOT NULL
);

-- Tin đã chuẩn hoá — gồm cả tin trùng và tin reject (không xoá).
CREATE TABLE job_posts (
    id                   INTEGER PRIMARY KEY,
    source               TEXT NOT NULL,
    source_job_id        TEXT,
    url                  TEXT,
    title                TEXT,
    company_id           INTEGER REFERENCES companies(id),
    industry_label_id    INTEGER REFERENCES labels(id),
    occupation_label_id  INTEGER REFERENCES labels(id),
    location_label_id    INTEGER REFERENCES labels(id),
    location_count       INTEGER,               -- số địa điểm trên tin; chỉ địa điểm đầu được dùng
    experience_label_id  INTEGER REFERENCES labels(id),
    level_label_id       INTEGER REFERENCES labels(id),
    level_from_title     TEXT,                  -- cấp bậc suy từ tiêu đề theo bảng từ khoá cố định (PLAN Q5)
    skills_raw           TEXT,
    salary_raw           TEXT,
    salary_type          TEXT,                  -- range | fixed | up_to | from | negotiable | not_stated
    salary_from          REAL,                  -- VND/tháng
    salary_to            REAL,                  -- VND/tháng
    salary_currency      TEXT,
    salary_tax           TEXT NOT NULL DEFAULT 'unknown', -- gross | net | unknown
    posted_date          TEXT,
    deadline             TEXT,
    status               TEXT NOT NULL DEFAULT 'unknown', -- active | expired | removed | unknown
    description_text     TEXT,
    raw_path             TEXT,
    raw_sha256           TEXT,
    dq_status            TEXT NOT NULL,         -- ok | warn | reject
    dq_flags             TEXT NOT NULL DEFAULT '', -- danh sách cờ, phân cách bằng dấu phẩy
    dedup_key            TEXT,
    first_seen_at        TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    first_run_id         INTEGER NOT NULL REFERENCES runs(id),
    last_run_id          INTEGER NOT NULL REFERENCES runs(id),
    normalizer_version   INTEGER NOT NULL,
    UNIQUE (source, source_job_id)
);
CREATE INDEX idx_job_posts_dedup ON job_posts(dedup_key);
CREATE INDEX idx_job_posts_first_seen ON job_posts(first_seen_at);

-- Báo cáo tháng đã chốt: giữ nguyên khi ánh xạ đổi sau đó (BR-10).
CREATE TABLE reports (
    id          INTEGER PRIMARY KEY,
    period      TEXT NOT NULL UNIQUE,           -- YYYY-MM
    as_of_date  TEXT NOT NULL,
    payload     TEXT NOT NULL,                  -- JSON snapshot số liệu
    xlsx_path   TEXT,
    created_at  TEXT NOT NULL
);

-- Mọi thống kê chỉ đọc view này (BR-04): bỏ reject, giữ 1 tin sớm nhất mỗi dedup_key (BR-03).
CREATE VIEW v_jobs_unique AS
WITH ranked AS (
    SELECT p.*,
           ROW_NUMBER() OVER (PARTITION BY p.dedup_key ORDER BY p.first_seen_at, p.id) AS rn
    FROM job_posts p
    WHERE p.dq_status <> 'reject'
)
SELECT r.id,
       r.source,
       r.source_job_id,
       r.url,
       r.title,
       c.name_raw                                   AS company_name,
       r.company_id,
       li.master_code                               AS industry_code,
       lo.master_code                               AS occupation_code,
       ll.master_code                               AS province_code,
       le.master_code                               AS experience_code,
       COALESCE(lv.master_code, r.level_from_title) AS level_code,
       r.salary_type,
       r.salary_from,
       r.salary_to,
       r.salary_tax,
       CASE r.salary_type
           WHEN 'range' THEN (r.salary_from + r.salary_to) / 2.0
           WHEN 'fixed' THEN r.salary_from
       END                                          AS salary_mid,
       r.posted_date,
       r.deadline,
       r.status,
       COALESCE(r.posted_date, date(r.first_seen_at)) AS event_date,
       r.first_seen_at,
       r.last_seen_at,
       r.dq_status,
       r.dq_flags
FROM ranked r
LEFT JOIN companies c ON c.id = r.company_id
LEFT JOIN labels li ON li.id = r.industry_label_id   AND li.status = 'mapped'
LEFT JOIN labels lo ON lo.id = r.occupation_label_id AND lo.status = 'mapped'
LEFT JOIN labels ll ON ll.id = r.location_label_id   AND ll.status = 'mapped'
LEFT JOIN labels le ON le.id = r.experience_label_id AND le.status = 'mapped'
LEFT JOIN labels lv ON lv.id = r.level_label_id      AND lv.status = 'mapped'
WHERE r.rn = 1;
