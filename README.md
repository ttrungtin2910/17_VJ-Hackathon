# Garblo Backend

FastAPI + PostgreSQL (`ai_jobs`) + worker riêng + private S3/RustFS. OpenAI direct là
provider mặc định. FPT Qwen chỉ phân tích ảnh khi request catalogue chọn rõ `fpt`.
Không có fallback chéo provider, Redis/Celery hoặc ảnh giả khi thiếu cấu hình.

## Chạy local — Python >= 3.12

Tự động commit local sau lượt OpenCode và kiểm tra/push khi đủ 3 commit/repo:
xem [AutoGit FE/BE](tools/opencode-autogit/README.md). Mặc định cài ở chế độ dry-run.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
# Điền DATABASE_URL, S3_* và JWT_SECRET_KEY trong .env trước khi chạy.
alembic upgrade head
python -m garblo.storage_init  # Chỉ cho storage local cần tạo bucket mới.
uvicorn garblo.main:app --reload
```

Terminal khác, cùng thư mục và môi trường:

```bash
source .venv/bin/activate
garblo-worker
```

API/worker đọc `.env` tương đối với thư mục chạy lệnh. Settings được cache; thay env
phải khởi động lại **cả hai process**. `/mock` chỉ bật khi `ENABLE_API_MOCK_UI=true`;
đặt `false` khi triển khai. `APP_ENV=production` tự nó không tắt `/mock`.

## Cấu hình AI

| Tác vụ | Cấu hình bắt buộc |
|---|---|
| Catalogue OpenAI mặc định | `OPENAI_API_KEY`, `OPENAI_VLM_MODEL` |
| Catalogue FPT explicit | Chỉ `FPT_VLM_API_KEY` |
| Lookbook planning + image | Ba biến `OPENAI_API_KEY`, `OPENAI_VLM_MODEL`, `OPENAI_IMAGE_MODEL` |

FPT mặc định dùng `https://mkp-api.fptcloud.com/v1`, model
`Qwen2.5-VL-7B-Instruct`, timeout 60 giây. Overrides là optional như `.env.example`.
Có FPT key **không đổi default OpenAI**. API thiếu config trả `503` với
`detail.code=provider_not_configured`, không enqueue. Catalogue không cần image model.

OpenAI model ID phải được operator xác minh trong organization/project có credit:

1. Chọn chat model hỗ trợ **ảnh đầu vào và Chat Completions**, cấu hình `OPENAI_VLM_MODEL`.
2. Chọn model hỗ trợ **Images generations**, cấu hình `OPENAI_IMAGE_MODEL`.
3. Adapter dùng `size=1024x1024`; model `dall-e*` yêu cầu `response_format=b64_json`,
   các model khác phải trả `b64_json` mặc định. Không dùng URL tùy ý từ provider.
4. Model xuất hiện trong danh sách account chưa chứng minh quyền generate image;
   phải smoke test request thật. Không dùng tên Azure deployment thay model ID.

Repository chưa xác minh model/quyền của organization; hai model env cố ý để trống,
không có default model được coi là đã chạy thật. Không paste key vào issue/log/report.

## Docker Compose

```bash
cp .env.example .env
# Đổi JWT_SECRET_KEY, S3_SECRET_KEY; điền AI config nếu cần.
docker compose up --build -d --wait api worker
```

- API: `http://localhost:8000`; có thể đặt `API_PORT=18080` trong `.env` nếu trùng port.
- PostgreSQL local: `127.0.0.1:15433` (override `POSTGRES_PORT`); credentials local mặc định
  `garblo/garblo-local-only`, DB `garblo`. Đổi `POSTGRES_PASSWORD` trước triển khai,
  dùng chuỗi URL-safe. App container dùng hostname `postgres`, không dùng DB URL host.
- RustFS: `http://localhost:19000`, console tắt; bucket mới mặc định private.
  `storage-init` tạo bucket và `migrate` chạy Alembic trước API/worker.
- `S3_PUBLIC_ENDPOINT_URL` là địa chỉ browser truy cập được để ký download URL;
  không thay bằng internal hostname `rustfs`. Khi đưa lên server, đặt endpoint HTTPS thật.
- Cả API và worker dùng chung `env_file`; có thể chọn file khác bằng
  `GARBLO_ENV_FILE=/absolute/path/to/env` và `docker compose --env-file ...`.
- Các port mặc định chỉ bind loopback. Cần TLS/reverse proxy, backup và IAM phù hợp
  khi triển khai; Compose local không phải bằng chứng đã deploy AWS.

Sau khi thêm `FPT_VLM_API_KEY` hoặc đổi AI config:

```bash
docker compose up -d --no-build --force-recreate api worker
```

Không cần rebuild image. **`docker compose restart` đơn thuần không nạp lại `env_file`**;
phải recreate container như trên. Không xóa volumes khi cập nhật. Dừng bằng
`docker compose down` (không thêm `-v` nếu cần giữ dữ liệu).

## Luồng nghiệp vụ và compatibility

1. Upload ảnh → `catalogize` không body/`{}` dùng OpenAI, hoặc body chọn FPT.
2. Poll job → nhận `candidate_ids` → đọc/sửa draft → commit → wardrobe.
3. Chọn garments đã commit → tạo lookbook bằng preset → poll → ảnh preview + approval.
4. Accept/reject approval lưu quyết định; like/dislike là feedback riêng.

**Thay đổi nghiệp vụ:** catalogue không tự tạo garment nữa. Client cũ vẫn gửi request
không body được, nhưng phải tích hợp draft/commit thay vì chờ `output_ref.garment_id`.
Garments đã có trước migration được giữ nguyên. Phân tích lặp cùng asset trả 409;
edit draft không gọi AI. Job lỗi được retry cùng provider/model, tổng tối đa 3 attempts
(cấu hình `WORKER_MAX_ATTEMPTS`), không reset budget qua retry HTTP.

Presets: `everyday`, `office`, `weekend`, `evening`. `theme`/`occasion` cũ chỉ nhận
giá trị được ánh xạ allowlist; free prompt/pose/background bị từ chối rõ bằng 422.
Route variation được giữ nhưng tạm khóa prompt tự do bằng 422 sau owner/consent/config checks.

Migration `20261030_0002` là additive. Job Azure legacy không bị đổi provider;
job cũ thiếu model snapshot sẽ fail an toàn. Operator cần phục hồi model gốc đã xác minh
trước khi retry; không suy từ model OpenAI mới hay tự thay đổi snapshot.

Hợp đồng API và curl: [docs/api-rebuild.md](docs/api-rebuild.md).

## Kiểm thử

```bash
python -m pytest -q
ruff check src tests migrations
ruff format --check src tests migrations
```

Test mặc định dùng SQLite và HTTP/provider mocks, không cần key thật. Integration
skip có lý do nếu chưa cấu hình. Không ghi skip thành PASS.

```bash
# PostgreSQL DB test riêng, tên DB phải kết thúc bằng _test.
export GARBLO_TEST_DATABASE_URL='postgresql+psycopg://user:password@localhost:15432/garblo_test'
# RustFS/S3 test riêng, credential có quyền tạo/xóa bucket test UUID.
export GARBLO_TEST_S3_ENDPOINT_URL='http://localhost:19000'
export GARBLO_TEST_S3_ACCESS_KEY='test-access-key'
export GARBLO_TEST_S3_SECRET_KEY='test-secret-key'
python -m pytest -q
```

PostgreSQL tests chạy migration trong schema UUID riêng rồi dọn đúng schema đó.
Storage test chỉ tạo/xóa bucket `garblo-test-<UUID>` do chính test tạo. Không dùng DB/bucket
production. Migration đầu đã phát hành được giữ nguyên cả nội dung và formatting.

## Catalogue v2 — Chặng A

Job mới mặc định snapshot `catalogue-v2`: tối đa 20 component **ở mức metadata**,
thuộc tính chưa biết dùng null, không có confidence giả. Job v1/legacy giữ schema/prompt
tương ứng; đặt `CATALOGUE_SCHEMA_VERSION=1` để ngừng enqueue v2 khi rollback, không sửa
snapshot job đang chạy. Cần restart cả API/worker sau đổi env.

- Review có `details`, `review`, `schema_version`, `component_asset_id`; attributes cũ vẫn giữ.
- PATCH candidate `status=draft|rejected`; committed không sửa qua draft.
- Commit bắt buộc category đã xác nhận, validate cả batch và giữ ID khi retry.
- Asset có thể `partially_catalogued`; chỉ `catalogued` khi không còn draft chưa xử lý.
- `/v1/jobs/{id}/calls` là audit từng request; `/v1/me/ai-metrics` chỉ thống kê user hiện tại.
- Call budget và token reservation ghi DB trước request. Usage thiếu giữ reservation,
  không coi là miễn phí. Deadline bắt đầu từ request đầu, không reset khi retry/reclaim.
- Token reservation do operator cấu hình, không phải hard cap chi phí USD upstream;
  usage có thể vượt estimate. Khi vượt budget không gửi request tiếp theo.
- P1 opt-in: checksum dedup cùng user, screening relevance bằng model riêng, escalation tối đa
  một lần/attempt và HTTP segmentation service có mask. Không fallback provider.
- Screening dùng cùng typed analysis contract, **không phải safety moderation**.
  `CATALOGUE_NATIVE_STRUCTURED_OUTPUT` chỉ bật sau xác minh capability thật.

Chi tiết: [API catalogue v2](docs/api-catalogue-v2.md),
[báo cáo nghiệm thu A](docs/architecture/07-acceptance-stage-a.md),
[ADR](docs/architecture/adr-001-deterministic-pipelines.md).

## Recommendation — Chặng B

Sau `alembic upgrade head` (bao gồm `20261031_0004`), API `/v1/recommendations`
phối tối đa Top 3 từ garments **active thuộc user**, không tạo ảnh, không cần AI key.
Context version 1 khớp frontend: Sự kiện + Phong cách bắt buộc, Thời tiết/Di chuyển tùy chọn.
Location chỉ lưu bối cảnh thủ công, không tự lấy vị trí/thời tiết. Chưa tích hợp HTTP frontend.

- Create session cần `Idempotency-Key`; cùng key/body trả cùng session và item IDs.
- Tổ hợp bị giới hạn: 8 món/role, 512/template, tối đa 1024 tổ hợp; thiếu đồ trả lý do,
  không bịa đủ 3. Có template áo+quần+giày hoặc váy liền+giày.
- Typed edit chỉ thay một slot, giữ các món khác, kiểm tra `expected_revision` và locks.
- Feedback PUT là latest signal, retry/flip không nhân mẫu. Snapshot feature/weights giữ lịch sử,
  cap từng feature 3, tổng personalization 8. Không tự tăng wear_count từ like.
- Hội thoại tùy chọn dùng `STYLIST_INTENT_MODEL` + OpenAI key đã xác minh; chỉ hiểu intent,
  không tạo ảnh. Thiếu cấu hình trả 503 trước enqueue, không fallback ngầm.
- Intent đi qua worker/audit/budget/consent/fencing và commit outfit cùng job status trong
  một transaction. User locks và typed constraints không bị model bỏ qua.

Contract/curl: [API recommendation](docs/api-recommendations.md).
Nghiệm thu tổng: [A + B: 368 tests, PostgreSQL/S3/HTTP runtime](docs/architecture/08-acceptance-stage-b.md).

Rollback B: ngừng nhận request/message mới và drain worker trước khi rollback app. Giữ các
bảng additive và dữ liệu snapshot; không downgrade DB đang có sessions/feedback cần giữ.
Rollback A: ngừng enqueue v2 bằng schema v1 và tắt optional stages tương ứng, drain jobs v2
trước khi dùng worker không hỗ trợ v2. Không sửa snapshot job đang chạy.

## Giới hạn hiện tại

- V2 nhận nhiều metadata component; không tự giả lập ảnh tách. Segmentation chỉ hoạt động khi
  operator cấu hình service/model thật, trả crop/mask theo contract; chưa kiểm chứng chất lượng
  segmentation live. Không VTON/garment reference fidelity. Lookbook cũ dựng từ metadata.
- FPT hỗ trợ JSON envelope/root và parse SSE nếu response thực sự là SSE; runtime key
  của đội vẫn cần xác minh `stream:false`. Không suy đoán wire behavior từ mock.
- Ảnh được decode, giới hạn pixel, bỏ metadata/EXIF, áp dụng orientation; JPEG chuyển PNG
  tránh tái nén mất pixel. Ảnh sau chuẩn hóa vẫn phải dưới giới hạn upload.
- Consent được kiểm tra trước mỗi external inference và trước persist. Không thể thu hồi
  request đã gửi tới provider; không hứa xóa dữ liệu provider khi API không hỗ trợ.
- Lease + fencing chống worker cũ ghi đè DB; không bảo đảm exactly-once billing provider.
  S3 và DB không có distributed transaction: crash có thể để lại object chưa được DB tham chiếu;
  cần đối soát orphan trước khi xóa, không áp lifecycle xóa toàn bộ generated prefix.
- Live OpenAI/FPT và AWS chỉ được xác nhận khi có evidence riêng, không suy từ test mock.
# Bổ sung local WSL — policy catalogue

`CATALOGUE_FPT_FALLBACK_ENABLED` mặc định `false`. Bật cùng FPT key chỉ ảnh hưởng job catalogue OpenAI **mới**: snapshot provider/model fallback (không key), tối đa hai worker attempts OpenAI, attempt thứ ba FPT sau hai lỗi vision transient có audit. Cap gốc <3 không tăng; có policy thì cap tối đa 3, đồng thời vẫn chịu call/token/deadline/consent/lease. Không fallback vì auth/billing/safety/schema/input/consent; không fallback render/intent/legacy/explicit FPT. Thiếu FPT key giữ OpenAI-only và cảnh báo một lần mỗi process. Native structured output của FPT luôn tắt; Pydantic validate cùng schema. `job.model_provider`/snapshot vẫn là provider yêu cầu; output có `effective_provider/effective_model`, physical-call ledger ghi provider/model thực. Unit/provider giả không chứng minh wire-live.
