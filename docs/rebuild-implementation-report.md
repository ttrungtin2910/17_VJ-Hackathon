# Garblo BE Implementation Report

Ngày: 30/10/2026. **IMPLEMENTED + TESTED_WITH_MOCK** cho AI adapters; PostgreSQL,
RustFS và HTTP runtime đã kiểm thử thật. **Chưa TESTED_WITH_REAL_PROVIDER**, chưa deploy AWS.

## 1. Baseline

- Repo: `garblo-backend/`, branch `rebuild/openai-fpt-ai-factory`, baseline `66c0e5f`.
- Working tree lúc bắt đầu: PDF proposal đã bị xóa, `docs/` chưa tracked; không thay đổi
  quyết định xóa PDF hoặc sửa hand-off của người dùng. Không commit/push.
- Call chain trước sửa (line tại baseline):
  1. `api/routes.py:191` catalogue không có body, dependency yêu cầu đủ Azure config.
  2. `routes.py:198` consent HTTP → owner asset → enqueue `input_ref={asset_id}`.
  3. `worker.py:30` claim PostgreSQL `FOR UPDATE SKIP LOCKED` → commit running.
  4. `worker.py:87` lấy storage bytes → Azure analysis.
  5. `worker.py:89` tạo thẳng garment, chưa có draft/review/commit.
  6. `worker.py:64` lỗi provider thành failed; chưa có backoff/reclaim/fencing.
  7. `worker.py:177` khởi tạo một Azure provider cho toàn worker, thiếu Azure làm worker dừng.
  8. `ai.py:105` Azure Images generation từ metadata, không garment reference image.
- Khác/lưu ý so với hand-off: `AiJob` đã có `model_provider`, `attempts`, `locked_at`,
  nên tái dùng; chưa có provider/model snapshot hoặc retry budget. Upload chỉ kiểm MIME header.
  Initial migration dùng JSON trong khi ORM có variant JSONB; filter PostgreSQL cast rõ sang JSONB.
  Tests baseline chỉ ba ca, SQLite fixture chưa shared connection và còn đọc env thật.

## 2. Changes implemented

Đường dẫn dưới đây tương đối với repository; line là source sau triển khai.

| Task | Status | Files/lines | Behavior | Evidence |
|---|---|---|---|---|
| P0-A OpenAI mặc định | IMPLEMENTED / TESTED_WITH_MOCK | `api/routes.py:250`, `jobs.py:30`, `ai_providers/openai_direct.py:21` | Catalogue no body/`{}` luôn OpenAI; Images cũng direct OpenAI | `test_ai_provider_selection.py`, `test_openai_direct.py` |
| P0-A FPT | IMPLEMENTED / TESTED_WITH_MOCK | `config.py:8`, `ai_providers/fpt_qwen.py:22` | Key optional; đúng URL/model/data URI; envelope/root/content parts/fences/SSE; một repair tối đa | `test_fpt_qwen_adapter.py` |
| P0-A Retry/recovery | IMPLEMENTED / TESTED_WITH_MOCK + PostgreSQL | `worker.py:88`, `worker.py:144`, `api/routes.py:529` | Snapshot pin, bounded backoff, lease/fencing, no cross-provider fallback | `test_worker_provider_retry.py`, `test_postgres_integration.py` |
| P0-B Image/draft/commit | IMPLEMENTED / TESTED_WITH_MOCK + PostgreSQL | `images.py:9`, `models.py:168`, `api/routes.py:289` | Decode/MIME/EXIF, persisted editable draft, commit idempotent có row locks | `test_catalogue_draft_commit.py`, concurrent PostgreSQL commit |
| P0-C Wardrobe | IMPLEMENTED / SQLite + PostgreSQL | `api/routes.py:360`, `schemas.py:69` | List/get/PATCH/category/tags đúng owner, drafts không thành garments | `test_wardrobe_ownership.py`, PostgreSQL JSON filter |
| P0-D Lookbook | IMPLEMENTED / TESTED_WITH_MOCK + PostgreSQL | `presets.py:20`, `worker.py:316`, `api/routes.py:464` | Preset-safe; render/storage/look_items/approval; expose IDs; trạng thái fail/retry nhất quán | `test_lookbook_pipeline.py`, concurrent PostgreSQL approval |
| Feedback | IMPLEMENTED / TESTED_WITH_MOCK | `api/routes.py:608` | Like/dislike lưu `style_signals`, độc lập approval | `test_lookbook_pipeline.py` |
| P0-E Security | IMPLEMENTED / tests | `consent.py:7`, `worker.py:154`, `storage.py:74` | Owner checks, consent trước external calls, redact errors, private signed URLs | Ownership/consent tests; private RustFS GET bị từ chối |
| P0-E Ops | IMPLEMENTED / runtime thật | `Dockerfile`, `compose.yaml`, migration `20261030_0002` | API/worker chung env, migration/storage init, tắt mock UI | Build và Compose startup thành công; `test_runtime_smoke.py` |
| Live OpenAI/FPT | BLOCKED | Runtime config | Chưa có key và OpenAI model IDs trong môi trường đã kiểm tra | Không gọi provider thật, không tiêu credit |

### AS-IS → Target → File sửa

| AS-IS | Target | File |
|---|---|---|
| Azure-only, global provider | Per-job OpenAI direct/FPT explicit | `ai.py`, `ai_providers/`, `config.py`, `jobs.py` |
| Catalogue ghi wardrobe ngay | Draft → edit → explicit commit | `models.py`, `schemas.py`, `api/routes.py`, `worker.py` |
| Retry thủ công không cap/reclaim | Bounded automatic/manual attempts, lease fencing | `worker.py`, `models.py`, migration mới |
| Worker không re-check consent | Check trước mỗi inference và persist | `consent.py`, adapters, `worker.py` |
| Upload tin header | Decode bytes, strip EXIF, MIME thật | `images.py`, `api/routes.py` |
| Free prompt, thiếu garment/approval IDs | Preset allowlist, additive response fields | `presets.py`, `schemas.py`, `api/routes.py` |
| Chưa Docker/integration | Compose local + PostgreSQL/RustFS suites | packaging, tests, README |

## 3. API contract thực tế

Chi tiết request/response/curl: [api-rebuild.md](api-rebuild.md).

- `POST /assets/{id}/catalogize`: body optional; `vlm_provider=openai|fpt`, default OpenAI;
  response vẫn `202 {job_id,status}`. Validate owner/consent/config trước enqueue.
- `GET /jobs/{id}` giữ field cũ, thêm provider/model/attempts/next-attempt. Catalogue output
  dùng `candidate_ids` hoặc `no_items`; **không còn tự tạo garment**.
- Candidate list/PATCH/commit có owner checks. Commit lặp/đồng thời trả cùng garment IDs;
  candidate đã commit không được PATCH, sửa garment thay thế.
- Wardrobe vẫn trả array; filter nhiều tags là AND, không trả draft chưa commit.
- Lookbook chỉ nhận presets/legacy aliases allowlist. Reject free instruction, pose/background;
  variation free prompt tạm khóa 422. Đây là hạn chế nghiệp vụ có công bố, không giả toàn bộ
  semantics client cũ vẫn tương thích.
- Look response thêm `garment_ids`/`approval_id`; job render thêm `lookbook_id` và các output IDs.
  Approval accept/reject persist, feedback like/dislike độc lập.
- Error provider mới dùng `detail={code,message}`; các error cũ có thể là chuỗi.
  Retry giữ cùng job ID/provider/model và không reset tổng attempt budget.

## 4. Tests executed

Môi trường Python 3.12.3, venv `/tmp/opencode/garblo-venv`. Dùng PostgreSQL 16.6 container
test riêng ở loopback, RustFS image pin digest trong Compose; không truy cập DB của dự án khác.

| Command / case | Result | Evidence / giới hạn |
|---|---|---|
| `pip install -e '.[dev]'` trong venv | PASS | Cài package và dependencies thành công |
| `python -m pytest -q` với đủ `GARBLO_TEST_*` | **71 passed**, 1 warning | Gồm 65 unit/mock + 4 PostgreSQL + 1 RustFS + 1 HTTP runtime |
| `python -m pytest -q -m 'not integration'` | **65 passed**, 6 deselected | Không cần PostgreSQL/storage/API chạy ngoài process |
| PostgreSQL migration | PASS | Mỗi integration schema chạy initial → `20261030_0002`, không `create_all` thay migration |
| PostgreSQL concurrent commit | PASS | Hai HTTP commit đồng thời → cùng IDs, đúng một garment |
| PostgreSQL SKIP LOCKED/reclaim/fencing | PASS | Locked job không bị claim; expired claim cũ không ghi được kết quả |
| Concurrent OpenAI/FPT worker | PASS, mock inference | Hai worker đồng thời giữ riêng snapshot/output provider |
| PostgreSQL concurrent approval | PASS | Một quyết định 200, quyết định còn lại 409; look/approval nhất quán |
| RustFS bytes/privacy/presign | PASS | GET anonymous bị từ chối; signed GET trả đúng bytes ảnh synthetic |
| Runtime HTTP | PASS, không inference | Register/login/consent/upload/download; OpenAI/FPT thiếu config → 503; `/mock` → 404 |
| `ruff check src tests migrations` | PASS | All checks passed |
| `ruff format --check src tests migrations` | PASS | 39 files already formatted; initial migration được exclude vì bất biến |
| `git diff --check` | PASS | Không whitespace errors |
| `docker build -t garblo-backend:local .` | PASS | Build image Python non-root thành công |
| Compose `up --no-build -d --wait api worker` | PASS | PostgreSQL, RustFS, init/migration, API và worker chạy được |
| Live OpenAI | BLOCKED | `OPENAI_API_KEY`, VLM/image model chưa cấu hình; không có live job evidence |
| Live FPT | BLOCKED | `FPT_VLM_API_KEY` chưa cấu hình; chưa xác minh non-stream bằng key đội |
| AWS | NOT RUN | Không yêu cầu/khẳng định đã deploy |

Warning còn lại từ passlib dùng `crypt` đã deprecated trên Python 3.12; không phải test failure.
Pin `bcrypt<4.1` để giữ tương thích passlib 1.7, thêm Pillow cho xác minh/loại metadata ảnh;
không nâng version hàng loạt. Initial migration được giữ nguyên. Alembic env sửa escape `%`
trong URL để chạy được URL-encoded password/query options, phát hiện qua integration test.

Để tái hiện: cấu hình các biến `GARBLO_TEST_DATABASE_URL`, `GARBLO_TEST_S3_ENDPOINT_URL`,
`GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY` như README. Thêm
`GARBLO_TEST_API_URL=http://127.0.0.1:18080` khi chạy Compose **test không AI keys**.
Không có service config thì integration được skip có lý do, không coi là kiểm thử thật.
Các container kiểm thử do agent tạo được dừng sau nghiệm thu; không dừng dịch vụ của
dự án khác. Compose named volumes test được giữ lại, không chạy `down -v`.

## 5. Configuration & secrets

- `.env.example` chỉ có placeholders: `OPENAI_API_KEY`, `OPENAI_VLM_MODEL`,
  `OPENAI_IMAGE_MODEL`, optional `FPT_VLM_API_KEY` và các override đã document.
- OpenAI model không hardcode: chưa có quyền/model thực được xác minh từ organization.
  Operator cần chọn model hỗ trợ Chat Completions vision và Images generation rồi live smoke.
- FPT chỉ thêm key, restart API + worker là chọn được qua `vlm_provider=fpt`;
  request không chọn vẫn OpenAI. Model và URL FPT có defaults trong source.
- Compose cần `up -d --no-build --force-recreate api worker` để nạp env mới;
  `docker compose restart` không cập nhật `env_file`. Không cần rebuild.
- Test runtime dùng env tạm ngoài repository, không chứa credential thật.
  Không tạo `.env` trong repo, không ghi key/ảnh/Base64/signed URL vào report.

## 6. Known limitations & follow-ups

1. **P0 runtime verification còn BLOCKED ở AI thật:** cần key, model IDs/quyền organization,
   ảnh test có consent để chạy cả OpenAI default và FPT explicit. Mock không chứng minh
   chất lượng nhận diện, model access, tốc độ hay quyền image generation thực tế.
2. FPT có parser SSE và tests nhưng chưa xác minh endpoint đội có nhận `stream:false`.
   Nếu endpoint từ chối non-stream thay vì trả SSE, cần điều chỉnh negotiation sau smoke thật.
3. Một garment/ảnh; chưa segmentation/crops/multi-item, VTON/reference image fidelity.
   Lookbook dùng metadata, không tuyên bố giữ chính xác ảnh garment gốc.
4. Job Azure cũ thiếu model snapshot không thể tự suy ra model lịch sử. Giữ provider,
   fail an toàn; operator phải khôi phục snapshot đã xác minh trước khi retry.
5. Lease/fencing bảo vệ DB, không bảo đảm exactly-once provider billing hoặc thu hồi request
   đang chạy khi consent bị rút. Không hứa xóa dữ liệu ở provider.
6. S3/DB không atomic xuyên service: crash có thể để lại object orphan. Cần đối soát object
   không tham chiếu trước cleanup; không xóa dữ liệu đang dùng hoặc toàn bộ generated prefix.
7. AWS/TLS/backup/IAM production, pagination, detailed image edit, voice, social và scoring
   không được triển khai như phần P0 này. Compose chỉ là local MVP packaging.
