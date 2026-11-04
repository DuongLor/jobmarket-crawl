-- Hết hạn theo vòng quét: tin chỉ bị coi là hết hạn khi một vòng quét TRỌN danh sách bắt đầu sau lần cuối thấy tin
-- mà không thấy lại (quét sâu mất nhiều ngày/vòng → mốc "N ngày không thấy" sẽ đánh dấu nhầm tin còn tuyển).
ALTER TABLE crawl_listings ADD COLUMN sweep_started_at TEXT;
ALTER TABLE crawl_listings ADD COLUMN last_full_sweep_started_at TEXT;
