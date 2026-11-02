# Làm việc với backend Garblo

Các đường dẫn và lệnh bên dưới tính từ `garblo-backend/`; chạy Git tại đây, không ở gốc workspace.

## Cấu trúc và quy ước đóng góp

- Mã ứng dụng nằm trong `src/garblo/`; kiểm thử trong `tests/`; migration Alembic trong `migrations/versions/`; tài liệu API, kiến trúc và vận hành trong `docs/`. Worker dùng chung package với API nhưng chạy process riêng.
- Dùng Python 3.12+, thụt lề 4 dấu cách, giữ kiểu và cách đặt tên theo PEP 8: `snake_case` cho module/hàm/biến, `PascalCase` cho lớp, `UPPER_SNAKE_CASE` cho hằng. Ruff là nguồn chuẩn cho lint và format (dòng tối đa 100 ký tự).
- Đặt tên kiểm thử `test_*.py` và hàm `test_*`; thêm kiểm thử cho thay đổi hành vi, quyền truy cập, consent, xử lý lỗi và tính nhất quán dữ liệu. Thay đổi schema cần migration mới; không sửa migration đã phát hành.
- Lịch sử dùng tiền tố Conventional Commits như `feat(backend):`, `fix(backend):`, `docs(backend):`, `refactor(tooling):`. Giữ tiêu đề ngắn, nêu phạm vi và lý do thay đổi; mỗi repo có lịch sử Git riêng.
- PR cần mô tả hành vi thay đổi, cấu hình/migration có liên quan, lệnh kiểm tra đã chạy và kết quả; liên kết issue nếu có. Không đưa bí mật, token hoặc dữ liệu người dùng vào diff, log hay tài liệu.

## Chạy và cấu hình

- Python >=3.12: tạo/kích hoạt virtualenv theo `README.md`, rồi `python -m pip install -e '.[dev]'`; chép `.env.example` thành `.env`, cấu hình DB/S3/JWT; `alembic upgrade head`, rồi `uvicorn garblo.main:app --reload`. Chạy **`garblo-worker` ở process riêng**. Bucket local mới có thể tạo bằng `python -m garblo.storage_init`.
- Compose: sau khi cấu hình `.env`, chạy `docker compose up --build -d --wait api worker`; Compose chạy `migrate` và `storage-init` trước API/worker. PostgreSQL host mặc định `15433`, RustFS `19000`, khác cổng trong `.env.example` dùng chạy trực tiếp.
- Đổi env Compose: `docker compose up -d --no-build --force-recreate api worker`, không chỉ `restart`. `S3_PUBLIC_ENDPOINT_URL` phải truy cập được từ browser, không dùng hostname nội bộ `rustfs`; không thêm `-v` khi dừng nếu cần giữ dữ liệu.
- `.env` được đọc theo thư mục chạy lệnh; settings có cache, app/DB engine khởi tạo lúc import. Đổi env phải khởi động lại cả API và worker. Alembic lấy URL từ settings, ghi đè cấu hình trong `alembic.ini`; đổi kết nối qua `DATABASE_URL`.
- `/mock` phục vụ `src/garblo/static/mock.html`; mặc định bật, `APP_ENV=production` không tự tắt. Compose đặt `ENABLE_API_MOCK_UI=false` rõ ràng.
- Bộ điều phối FE–BE WSL riêng ở `local-wsl/`: sample/key/start, project `garblo-local-wsl`, không thay `compose.yaml`/env hiện hữu. Runbook `docs/HUONG_DAN_CHAY_LOCAL_WSL.md`; `/ready` kiểm DB/schema/storage, worker health có heartbeat, không gọi provider. Profile test dùng env/override/project/volume riêng, không `down -v`.

## API và xử lý AI

- `src/garblo/main.py` gắn `src/garblo/api/routes.py` dưới `/v1` mặc định. Luồng catalogue: upload → job → `candidate_ids` → sửa draft → commit → garment; phân tích không tự thêm món vào tủ. Contract/curl: `docs/api-rebuild.md`, đối chiếu với routes và `src/garblo/schemas.py` khi tích hợp.
- Hàng đợi là bảng PostgreSQL `ai_jobs` (`FOR UPDATE SKIP LOCKED`), không có Redis/Celery. Giữ lease/lock token khi sửa worker để chặn kết quả từ worker cũ. Lookbook tạo look `preview` và approval `pending`; approval khác với feedback like/dislike.
- OpenAI direct mặc định: catalogue cần `OPENAI_API_KEY` + `OPENAI_VLM_MODEL`; lookbook cần thêm `OPENAI_IMAGE_MODEL`. FPT explicit dùng `vlm_provider=fpt` + `FPT_VLM_API_KEY`. Chỉ catalogue OpenAI mới khi `CATALOGUE_FPT_FALLBACK_ENABLED=true` và có FPT key được snapshot policy tối đa hai attempts OpenAI rồi attempt ba FPT sau hai lỗi vision transient có audit; không tăng cap <3. Không fallback auth/billing/safety/consent/invalid output/lease/budget; không áp dụng render/intent hoặc job legacy. Azure chỉ dành cho legacy; không tạo kết quả giả. Thiếu config trả `503`/`provider_not_configured`, không enqueue.
- Tạo/retry job cần consent `ai_processing`. Retry giữ provider/model snapshot và tổng attempt budget (`WORKER_MAX_ATTEMPTS`, mặc định 3); không reset attempts hoặc tự thay model cho job legacy thiếu snapshot.
- Lookbook dùng preset allowlist (`everyday`, `office`, `weekend`, `evening`), không nhận prompt tự do; route variation được giữ nhưng hiện trả `422` sau các kiểm tra quyền/consent/config.

## Kiểm tra và migration

- `python -m pytest -q`; lint `ruff check src tests migrations`; format `ruff format --check src tests migrations`. Một ca: `python -m pytest -q tests/test_auth_and_ai_unavailable.py::test_ai_route_returns_503_before_openai_is_configured`.
- `tests/conftest.py` dùng SQLite bộ nhớ/`Base.metadata.create_all`, storage/provider giả; fixture settings dùng `_env_file=None` và xóa biến provider/tracing. Test này không chứng minh migration PostgreSQL hoặc AI thật hoạt động.
- Chạy không cần dịch vụ: `python -m pytest -q -m 'not integration'`. Integration tự skip nếu thiếu env: PostgreSQL dùng `GARBLO_TEST_DATABASE_URL` với DB tên kết thúc `_test`, migrate trong schema UUID riêng; S3 dùng `GARBLO_TEST_S3_ENDPOINT_URL`, `GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY`, tạo/xóa bucket UUID. Chỉ dùng tài nguyên test riêng; xem `README.md`.
- Runtime smoke cần `GARBLO_TEST_API_URL` tới Compose local test **không có AI keys**; test có tạo user/asset thật. Skip không phải PASS; provider mocks không phải bằng chứng đã gọi OpenAI/FPT thật.
- Migration đã phát hành `migrations/versions/20261018_0001_initial_schema.py` được giữ nguyên cả formatting; Ruff đã exclude tương ứng. Thay schema bằng migration mới.

## Tài liệu solution HTML

- Chạy `node build-garblo-solution.mjs` rồi `node verify-garblo-solution.mjs`. File `output/garblo-solution.html` được sinh từ script build, nhúng `output/garblo-architecture.html` và `output/garblo-database-relation.html`; sửa script/artifact nguồn rồi build lại.
