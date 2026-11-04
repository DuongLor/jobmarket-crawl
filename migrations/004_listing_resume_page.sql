-- Quét sâu: trang sẽ đọc tiếp ở lần chạy sau (NULL = chưa bắt đầu hoặc đã quét hết một vòng).
ALTER TABLE crawl_listings ADD COLUMN resume_page INTEGER;
