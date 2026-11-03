[C-Career] Xây dựng chức năng Crawl Data Market Intelligence – Thu thập và phân tích dữ liệu thị trường tuyển dụng


Mục tiêu: Thu thập tin tuyển dụng công khai, làm sạch và lưu lại, rồi hiển thị thống kê nhu cầu tuyển dụng và mức lương cho tư vấn viên và quản lý.

Input – Thu thập dữ liệu
Nguồn: TopCV, VietnamWorks (tin công khai). Chạy hằng ngày, ngoài giờ cao điểm.
Mỗi tin lấy: mã tin, tiêu đề, tên công ty, ngành, nghề, địa điểm, kinh nghiệm, kỹ năng, cấp bậc, lương, ngày đăng, hạn nộp, trạng thái, mô tả.
Không lấy thông tin cá nhân (email, số điện thoại, người đăng)
Giữ nguyên bản gốc của mỗi tin để làm lại khi cần.
Quá trình – Làm sạch, chuẩn hoá, lưu trữ (DEV làm)
Chuẩn hoá:
Lương về VND/tháng.
Ghi loại lương (khoảng từ–đến, tới X, từ X, một mức, thoả thuận, không ghi)
Kinh nghiệm về 6 nhóm (Xem trong file Excel: "CCareer_Market_Intelligence_Mockup")
cấp bậc về 7 cấp. (Xem trong file Excel: "CCareer_Market_Intelligence_Mockup")
Ngành, nghề, tỉnh về tên chuẩn của CCareer.
Nhãn chưa biết: không đoán. Vẫn lưu tin, ghi nhãn ở trạng thái “chưa ánh xạ”.
Cờ chất lượng: ok / warn / reject. Reject khi thiếu mã tin, tiêu đề hoặc tên công ty.
Tin trùng: tạo khoá từ tên công ty + tiêu đề + tỉnh. Không xoá tin trùng.
Lưu trữ: tin (kể cả trùng và reject), nhãn, lịch sử chạy crawl.
View v_crawl_jobs_unique: chỉ gồm tin không reject và không trùng (giữ tin thấy sớm nhất). Mọi thống kê chỉ đọc từ view này.
Ánh xạ nhãn bằng CSV:
Xuất danh sách nhãn chưa ánh xạ (loại, nguồn, nhãn gốc, số tin, gợi ý), xếp theo số tin giảm dần.
Nạp file BA điền (map hoặc ignore, kèm tên chuẩn). Dòng lỗi ghi ra file lỗi, không dừng cả file. Chạy lại không tạo dòng trùng.
Ánh xạ xong, số liệu đổi ngay, không cần crawl lại.
Gửi README cho cả nhóm đọc, gồm: luồng dữ liệu; từng bảng, từng cột và vì sao có; quy tắc chuẩn hoá lương và nhãn; cách chạy và chạy lại; cách xử lý lỗi; cách xuất và nạp ánh xạ nhãn.
Output – Giao diện
Màn hình	Hiển thị
Tổng quan	Thẻ số liệu (tin duy nhất, đang tuyển, mới 30 ngày, số công ty, lương trung vị, % có lương) + biểu đồ
Nhu cầu tuyển dụng	Số tin theo ngành, tỉnh, tháng; bản đồ nhiệt ngành × tỉnh
Phân tích lương	Theo kinh nghiệm, cấp bậc, tỉnh, nghề; lọc theo ngành. Hiện trung bình, trung vị, P25–P75, số tin, % có lương
Xu hướng	Ngành tăng/giảm, 3 tháng gần nhất so với 3 tháng trước
Báo cáo tháng	Báo cáo tự sinh, xuất PDF/Excel
Chất lượng dữ liệu & ánh xạ nhãn (Admin)	Chỉ số phủ, danh sách nhãn chờ duyệt, chọn tên chuẩn hoặc bỏ qua, nhật ký thay đổi
Yêu cầu nghiệp vụ
Đơn vị đếm là số tin duy nhất, không phải số người cần tuyển. Ghi rõ trên giao diện.
Tin chưa ánh xạ ngành vẫn được tính, hiện ở nhóm “(chưa phân loại)”.
Lương đại diện mỗi tin: khoảng từ–đến thì lấy trung điểm; một mức thì lấy chính mức đó. “Tới X”, “từ X”, thoả thuận, không ghi thì không đưa vào trung bình hay trung vị.
Nhóm có dưới 5 tin có lương số thì hiện “n<5”, không hiện số. Luôn hiện số tin và % có lương cạnh số liệu lương.
Xu hướng: so tổng tin 3 tháng gần nhất với 3 tháng liền trước. Lệch trên 10% thì ghi tăng hoặc giảm. Nếu 3 tháng trước có dưới 10 tin thì hiện “Chưa đủ dữ liệu”.
Mỗi màn hình có chân trang: nguồn, ngày chốt số liệu, đơn vị là số tin, lương là mức đăng tuyển, chưa phân biệt Gross/Net.
Bộ lọc dùng chung: thời gian, nguồn, ngành, tỉnh, kinh nghiệm, cấp bậc. Mọi bảng xuất được CSV.
Ngưỡng (5 tin, 10 tin, 10%) và không viết cứng trong mã.
Duyệt nhãn: BA duyệt chính; PM và Tech Lead cũng được ánh xạ vào tên chuẩn có sẵn nếu được cấp quyền. Chỉ Tech Lead thêm tên chuẩn mới. Mọi thay đổi ghi nhật ký (người, thời điểm, giá trị cũ → mới, lý do). Không ai được xoá dòng nhãn.
Báo cáo đã phát hành giữ nguyên khi ánh xạ đổi sau đó.
Phân quyền: Admin xem tất cả và duyệt nhãn; vai trò khác chỉ xem số liệu.
Ngoài phạm vi ticket này
So sánh Việt Nam – Nhật: chưa có nguồn dữ liệu phía Nhật.

