# Garblo Backend

Backend MVP cho tủ đồ số và AI lookbook. API dùng FastAPI; PostgreSQL giữ metadata/trạng thái và RustFS (S3-compatible) giữ ảnh.

## Local development

1. Tạo môi trường Python 3.12 và cài package: `pip install -e '.[dev]'`.
2. Sao chép `.env.example` thành `.env`, rồi điền PostgreSQL/RustFS. Azure chỉ cần khi gọi catalogue hoặc lookbook.
3. Chạy migration: `alembic upgrade head`.
4. Chạy API: `uvicorn garblo.main:app --reload`.
5. Mở `http://localhost:8000/mock` để kiểm thử API thủ công. UI này chỉ bật khi `ENABLE_API_MOCK_UI=true`.

`POST /v1/assets/{asset_id}/catalogize`, `POST /v1/lookbooks` và variation yêu cầu đầy đủ cấu hình Azure OpenAI. Khi thiếu cấu hình, chúng trả `503` và không tạo job giả.

## Worker

Chạy `garblo-worker` trong process riêng. Worker claim `ai_jobs` từ PostgreSQL, gọi Azure OpenAI, ghi ảnh kết quả vào RustFS và tạo approval request cho preview.

Docker/Compose sẽ được bổ sung khi Docker Engine sẵn sàng.

