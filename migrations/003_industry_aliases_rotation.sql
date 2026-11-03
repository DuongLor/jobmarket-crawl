-- Ánh xạ ngành theo bảng tên ngành CỦA NGUỒN (giống province_aliases): nhãn ngành nguồn → ngành chuẩn.
-- industry_code NULL = nhãn không phải ngành (vd danh mục ngoại ngữ "Tiếng Nhật") → nhãn được đặt 'ignored'.
CREATE TABLE industry_aliases (
    alias_norm    TEXT PRIMARY KEY,
    industry_code TEXT REFERENCES master_industries(code)
);

-- Xoay vòng danh sách (trang ngành / tỉnh): mỗi lần chạy ưu tiên danh sách chưa duyệt hoặc duyệt lâu nhất,
-- để giới hạn request mỗi lần không làm lần nào cũng chỉ đi lại vài danh sách đầu.
CREATE TABLE crawl_listings (
    source          TEXT NOT NULL,
    url             TEXT NOT NULL,
    label           TEXT,
    last_crawled_at TEXT NOT NULL,
    PRIMARY KEY (source, url)
);
