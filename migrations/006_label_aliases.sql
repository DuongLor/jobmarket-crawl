-- Bảng ánh xạ quy tắc dùng chung cho các loại nhãn khác (cấp bậc…): tên nhãn nguồn → mã chuẩn; code NULL = bỏ qua.
CREATE TABLE label_aliases (
    label_type TEXT NOT NULL,
    alias_norm TEXT NOT NULL,
    code       TEXT,
    PRIMARY KEY (label_type, alias_norm)
);
