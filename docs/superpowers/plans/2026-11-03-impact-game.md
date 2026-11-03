# Kế hoạch triển khai Gamification & Impact

**Mục tiêu:** mở Impact cho real, lưu wear trên server, giữ thành tựu và cung cấp ba nhiệm vụ tuần.

**Kiến trúc:** PostgreSQL giữ snapshot wear và progress theo user. FE dùng GarbloDataSource chung; demo dùng IndexedDB schema v4, real gọi API. Không dùng API Lookbook legacy hoặc provider AI.

**Đặc tả:** `garblo-frontend/docs/impact.md` trong repo frontend cùng workspace và `docs/api-rebuild.md` trong repo backend.

## Các phần triển khai

- [x] Migration 0007, model wear/progress và khởi tạo thành tựu từ dữ liệu cũ.
- [x] Ghi wear idempotent với user lock, snapshot revision/items và ngày/timezone; cập nhật garment counters cùng transaction.
- [x] API summary và lịch sử, huy hiệu bền vững, ba nhiệm vụ tuần 3 ngày/2 outfit/5 món.
- [x] Adapter FE real, mapper và cache invalidation; mở route/nút Impact.
- [x] Demo migration/progress, giữ lịch sử khi xóa nguồn; dashboard tuần và thông báo thành tựu.
- [x] Unit/API test, PostgreSQL migration/concurrency test và browser test bổ sung.

## Bàn giao

Chạy migration backend trước khi phát hành FE. Không đổi `.env`, deploy hay migrate DB đang dùng trong phiên triển khai; PostgreSQL kiểm thử nằm trong container/schema riêng. Không tạo wear từ feedback/approval. Không có điểm, streak, nhiệm vụ tùy chọn, queue offline real hoặc lịch sử chi tiết.
