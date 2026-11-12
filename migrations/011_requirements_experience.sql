-- 2026-11-11: thêm yêu cầu công việc + kinh nghiệm theo tháng (user: "mô tả, yêu cầu công việc, kinh nghiệm khoảng năm
-- tháng"). Mô tả đã có (description_text). Kinh nghiệm vẫn giữ nhóm 6 mức (experience_label_id) cho thống kê; các cột
-- dưới đây giữ ĐÚNG chữ nguồn ghi + khoảng tháng đọc được từ chữ đó (không đoán khi chữ không rõ → NULL).

ALTER TABLE job_posts ADD COLUMN requirements_text TEXT;        -- yêu cầu công việc (đã xoá SĐT/email)
ALTER TABLE job_posts ADD COLUMN experience_raw TEXT;           -- vd "1 - 2 năm", "Hơn 5 năm", "12 tháng"
ALTER TABLE job_posts ADD COLUMN experience_min_months INTEGER; -- mức tối thiểu (0 = không yêu cầu)
ALTER TABLE job_posts ADD COLUMN experience_max_months INTEGER; -- mức trên nếu nguồn ghi ("1 - 2 năm" → 24, "Dưới 1 năm" → 12)
