-- Tin có NHIỀU mục ngành (CareerViet: 1–3 mục/tin, 83% tin ≥ 2 mục) — giữ đủ danh sách theo thứ tự nguồn để thống kê
-- cơ cấu ngành đếm CHIA ĐỀU (mỗi tin 1/k cho k ngành chuẩn khác nhau) thay vì chỉ mục đầu (thứ tự hiển thị của site
-- không rõ quy luật; đo 2026-11-04: "Kinh doanh – Bán hàng" mục đầu 118 tin so với có trong danh sách 446 tin).
-- Lưu id nhãn (không lưu mã) → ánh xạ / duyệt nhãn sau này có hiệu lực ngay.
CREATE TABLE job_industry_labels (
    job_id   INTEGER NOT NULL REFERENCES job_posts (id),
    position INTEGER NOT NULL,
    label_id INTEGER NOT NULL REFERENCES labels (id),
    PRIMARY KEY (job_id, position)
);
