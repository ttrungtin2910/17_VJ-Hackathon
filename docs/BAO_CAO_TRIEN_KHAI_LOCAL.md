# Báo cáo triển khai local WSL

## Trạng thái

Đã triển khai lõi FE–BE và bộ điều phối theo yêu cầu ngày 01/11/2026, tiếp tục kiểm chứng ngày 02/11/2026. Không gắn nhãn toàn bộ DONE/live-ready: T09 còn fault scenarios chưa chạy, T10 AI live chưa có phép ảnh/budget/key được operator cung cấp. Không commit/push hoặc gọi provider trả phí.

## T00 — baseline mới

- BE: `rebuild/openai-fpt-ai-factory`, HEAD `4e7a1b8`; trước sửa chỉ có `docs/KE_HOACH_LOCAL_WSL.md` chưa tracked.
- FE: `main`, HEAD `7e178e3`; README có hai dòng về Gamification/Impact từ trước, giữ nguyên.
- WSL2 Ubuntu, Python 3.12.3, Node 26.5.0, npm 11.17.0; Docker 29.6.2, Compose 5.3.1, daemon 29.6.2.
- RAM 7,6 GiB, khả dụng khoảng 2,5 GiB; disk trống 840 GiB tại workspace. Cổng 8000/5432 thuộc stack khác; 5173/18080/19000 chưa listen lúc đo. Không dừng container khác.
- Môi trường kiểm tra: `python3 -m venv /tmp/opencode/garblo-venv` (exit 0); `/tmp/opencode/garblo-venv/bin/python -m pip install -e '.[dev]'` trong BE (exit 0, log `/tmp/opencode/garblo-baseline-install.log`). Không cài đè system Python.

| cwd | Lệnh | Exit | Kết quả / phạm vi |
|---|---|---|---|
| FE | `npm run typecheck` | 0 | PASS |
| FE | `npm run lint` | 0 | PASS |
| FE | `npm run test` | 0 | PASS: 3 files, 51 tests; demo/unit |
| FE | `npm run build` | 0 | PASS: Vite 7.3.7; chưa browser |
| BE | `/tmp/opencode/garblo-venv/bin/python -m pytest -q -m 'not integration'` | timeout | Lượt đầu timeout 120s; không nhận là PASS |
| BE | cùng lệnh, timeout 300s | 0 | PASS: 352 passed, 16 deselected, 1 warning passlib/crypt, 111,72s; SQLite/provider giả |
| BE | `/tmp/opencode/garblo-venv/bin/ruff check src tests migrations` | 0 | PASS |
| BE | `/tmp/opencode/garblo-venv/bin/ruff format --check src tests migrations` | 0 | PASS: 63 files |

Baseline không chứng minh PostgreSQL migration, worker process, browser hay AI live. T09/T10 cần bằng chứng riêng.

## Phân loại nghiệm thu hiện tại

- PASS: baseline, unit/offline, PostgreSQL migrations/races, storage private/CORS/expiry, runtime readiness, browser demo và real với inference mock, lifecycle bền vững (chi tiết bên dưới).
- NOT TESTED: OpenAI/FPT live, normal profile với OpenAI key của operator và các fault scenarios còn lại ở cuối báo cáo.
- DEFERRED: wear/Impact real, generated lookbook/VTON/chat theo scope.

## T01 — mật khẩu và consent

- Thêm giới hạn 72 byte UTF-8 ở schema register và security; login không verify chuỗi quá dài, giữ hash legacy. Validation response không echo request input/context (thay đổi nhỏ tại `main.py` để không phản chiếu mật khẩu).
- Dùng chung `latest_consent()` cho checkpoint và `GET /me/consents/latest`; bản revoked mới nhất không làm grant cũ sống lại. Revoke lặp giữ 204; tài liệu ghi rõ checkpoint semantics và nhãn local-wsl-1.
- BE: `python -m pytest -q tests/test_local_auth_consent.py tests/test_auth_and_ai_unavailable.py`: RED exit 1 (5 failed, 4 passed); GREEN exit 0 (9 passed, 1 warning). Một lượt giữa sửa fixture sắp thứ tự consent bằng timestamp xác định, không phụ thuộc đồng hồ host.
- Ruff check/format và `git diff --check`: exit 0, 64 files tại gate T01. PASS cho gate T01; không có provider live.

## T02 — upload operation và exact source lookup

- Thêm `uploads.py`, model `UploadOperation`, migration additive `20261101_0005`, DTO và ba routes recovery; giữ route upload cũ không key tương thích.
- Key owner-scoped + request hash normalized, lease/token 300s, publish có fencing, không giữ DB transaction qua S3. Replay giữ reserved asset/object key; không tự xóa orphan.
- BE: `python -m pytest -q tests/test_upload_recovery.py tests/test_catalogue_draft_commit.py`: RED exit 1 (7 failed, 14 passed); GREEN exit 0 (21 passed).
- PostgreSQL test riêng: container `garblo-local-wsl-test-postgres-gate`, image `postgres:16.6-alpine`, port loopback 25439, DB `garblo_test`, volume `garblo-local-wsl-test-postgres-gate`. Không sửa DB/volume stack khác. Fixture tạo/drop schema UUID do chính test tạo.
- Lệnh tái lập (BE): `GARBLO_TEST_DATABASE_URL=postgresql+psycopg://garblo:garblo-test-only@127.0.0.1:25439/garblo_test /tmp/opencode/garblo-venv/bin/python -m pytest -q tests/test_postgres_integration.py --tb=short`.
- PostgreSQL lượt đầu exit 1: 1 failed, 10 passed; race test tìm được deadlock khóa user ↔ operation khi publish cần FK KEY SHARE. Đổi khóa reservation sang NO KEY UPDATE tương thích FK, vẫn serialize cùng user. Lượt mới exit 0: **12 passed**, bao gồm concurrent upload, storage-write-before-crash/recovery, old lease fencing, migration upgrade/downgrade giữ legacy, concurrent commit và worker fencing. Storage/provider của suite này vẫn là mock; không phải worker container hay S3 thật.
- Ruff check/format, `git diff --check`: exit 0, 67 files ở gate T02. PASS gate T02 backend; FE recovery còn thuộc T06/T07.

## T03 — fallback vision có snapshot

- Thêm `catalogue/fallback.py`, setting mặc định false, snapshot policy/model/max attempts cho job mới và audit effective provider/model. Attempt cap là min(snapshot, cấu hình hiện tại); không reset budget và không tự tăng cap thấp hơn 3. Khi fallback được snapshot, cap tối đa 3 (hai OpenAI, một FPT).
- Chỉ chọn FPT khi audit hai attempts trước có lỗi vision transient, không có schema repair invalid, safety/consent/billing, segmentation hay unknown reservation. Giữ nguyên snapshot/provider yêu cầu của job. FPT native schema tắt; vẫn Pydantic validation. Ledger chạy guard trước mọi physical call.
- `python -m pytest -q tests/test_catalogue_fallback.py --tb=short`: RED exit 1, 14 failed (chưa có setting/policy).
- `python -m pytest -q tests/test_catalogue_fallback.py tests/test_worker_provider_retry.py tests/test_catalogue_v2_provider.py tests/test_catalogue_stages.py --tb=short`: GREEN exit 0, 180 passed ở lượt đầu; sau đó thêm kiểm consent/token/deadline/lease tại attempt fallback và history invalid.
- Chạy lại toàn bộ offline: `python -m pytest -q -m 'not integration' --tb=short`: exit 0, **385 passed, 19 deselected**, 1 warning, 119,95s. Ruff check/format và `git diff --check`: exit 0, 69 files. PASS T03 trong phạm vi offline/mock, chưa wire-live.

### Bổ sung rà an toàn T03

Wire-mock mới chứng minh lỗi 429 chỉ có `type=insufficient_quota` hoặc không rõ loại bị phân loại sai: RED 2 failed. Đã đọc `code hoặc type`; snapshot có fallback phải có `rate_limit_exceeded` đã biết mới coi 429 là quyền retry/fallback. 429 opaque với policy fallback trả `provider_rate_limit_unknown`, không tự đổi provider. Giữ hành vi job legacy không có policy. Nhóm provider/fallback/stages sau sửa: exit 0, **192 passed**; full offline cuối cùng **391 passed, 19 deselected**, 1 warning passlib/crypt. Không có request live trong các ca này.

## T04–T05 — container, readiness và vòng đời

- Canonical artifacts trong `local-wsl/`, không tạo Git repo ở workspace root; Compose trỏ sang repo FE cạnh BE. `compose.yaml`/env cũ giữ độc lập, không bị ghi đè.
- Có PostgreSQL, RustFS, storage-init, migrate, API, worker và web. Named volumes tự mang project prefix; runtime/test không chia sẻ tên volume. Runtime chỉ publish loopback 5173/18080/19000, DB internal-only; test dùng 5183/18081/19001 và DB 25440.
- Pin Python 3.12.14, Node 24.11.0, Nginx 1.28.0, RustFS digest đã kiểm; FE build `npm ci`, static SPA fallback. RustFS image thật có `/bin/sh`, curl và wget; probe `/health` đã healthy, không giả công cụ không có.
- `/ready` kiểm DB/schema Alembic/bucket; worker heartbeat kiểm idle/busy/dead-loop cùng dependency. Không gọi provider. `/health` thêm cấu hình catalogue riêng để không lấy image-generation readiness làm điều kiện composition.
- Env sample có local credentials ổn định, chỉ cần nhập OpenAI key; FPT tùy chọn. Worker cố định INFO, SDK OpenAI/HTTPX ở WARNING, không có app flag bật SDK debug. FE không có env_file hoặc mount backend; chỉ hai public build args. `.dockerignore` backend loại cả `local-wsl` để không gửi env điều phối vào build context.
- `start-all.sh`: project allowlist, absolute paths, lock theo project, Docker/config/port checks, internal DB/storage guard, test DB `_test`/bucket riêng, no paid keys trong test, migrations serialize một lần mỗi lượt start, bounded wait cả API/worker/web. Không source/eval dotenv; JSON resolved config chỉ trong memory, stderr cấu hình được che.
- `stop-all.sh`: resolve bằng sample, không cần đọc secret hoặc env runtime còn tồn tại; chỉ `down` đúng project, không `-v`, không prune/kill process khác. Volumes còn nguyên.

| Lệnh / cwd | Exit và bằng chứng |
|---|---|
| BE: `python -m pytest -q tests/test_local_readiness.py` | RED collection exit 2 khi chưa có module; GREEN exit 0, 4 passed sau thêm HTTP /ready test |
| local-wsl: `docker compose ... config --quiet` | Lượt đầu exit 1 vì chưa có canonical .env; sau chọn explicit test env/sample, config hợp lệ. Không in resolved secrets |
| local-wsl: `docker compose ... build api web` | Exit 0; build thật cả FE và BE, shared backend image dùng cho worker/init/migrate |
| BE: `bash -n local-wsl/start-all.sh local-wsl/stop-all.sh local-wsl/common.sh local-wsl/tests/test-live.sh local-wsl/tests/test-lifecycle.sh` | Exit 0 |
| BE: `bash local-wsl/tests/test-scripts.sh` | RED 3 failed/7 passed trước khi có scripts; cuối **14 passed**, exit 0. Docker CLI double, không runtime/inference |
| Absolute `start-all.sh`, cwd `/tmp/opencode` | Exit 0, migrations/storage-init exited 0, API/worker/web healthy trong project test thật |
| BE: `bash local-wsl/tests/test-lifecycle.sh` | Exit 0: start×2 → stop×2 → start → concurrent start×2; DB/metadata/asset IDs/checksums/ảnh không đổi, mỗi API/worker/web đúng một container; test stack được stop, volumes giữ nguyên |

Lifecycle harness dùng cwd có khoảng trắng, absolute scripts và đúng test prefix/env/override; full physical project path có khoảng trắng được kiểm bằng CLI double, chưa copy hai repo sang path vật lý khác để chạy Docker. Normal runtime project không được dùng làm test.

Số đo idle một lượt (không phải peak/minimum RAM): API ~149,6 MiB, worker ~161,9 MiB, PostgreSQL ~34,6 MiB, RustFS ~195,7 MiB; web đang được recreate tại lúc đo nên không tính tổng cả stack. Lệnh stats đầu dùng `--filter` không được CLI hỗ trợ, exit 125; lượt sau chọn IDs theo project trước khi đo, exit 0. Không suy idle thành tài nguyên tối thiểu hay peak build/browser.

## T06–T08 — adapter và luồng giao diện

- Hoàn thiện 19 methods theo disposition: auth/consent, HTTP catalogue/review/commit/wardrobe/media, real metadata local-only, recommendations + latest feedback; wear báo FEATURE_UNAVAILABLE, demo tools DEMO_ONLY. Không gọi demo DB trong real, không seed.
- Token memory-only, GET /me xác nhận identity; query/media keys có mode/API/user/epoch, logout/401 abort và clear cache/context. Real metadata namespace riêng, không Blob/token/signed URL; lỗi persist operation chặn HTTP trước upload.
- Upload giữ key/fingerprint trước request, same-file retry; lost 201 tra operation, lost 202 tra đúng source; không newest heuristic. Commit giữ selection của attempt qua mất response. Partial PATCH reconcile và chặn commit; name 80, màu rỗng null, chỉ clear category uncertainty khi user xác nhận.
- F02 sửa canonical cache/editor rehydrate an toàn, queue/sequence và cancel refetch cũ; F08 ref lock synchronous + adapter in-flight guard. Poll 1,5→3→5→10s, terminal/network/auth/unmount được phân biệt; retry cùng job, cap không reset. no_items terminal không còn xuất hiện như nháp chưa hoàn tất.
- Media fetch Blob từ URL ký, không Bearer/cookie sang S3, bounded renewal; object URL cleanup, media cache có GC. Private image đã load thật trong browser (naturalWidth >0), không chỉ kiểm HTTP FE 200.
- Recommendation gửi `{context}` + stable key, giữ snapshot riêng từng outfit/revision/reason/missing/score notice; API list phân trang. Không đòi áo+quần khi backend có váy liền+giày. Revision >1 khóa feedback, signal cũ không được suy thành like của composition mới. Real không mount wear/Impact hooks.
- Context draft và operation refs local theo user; sessions/outfits/feedback ở PostgreSQL. UI nói rõ nơi lưu và checkpoint consent, không hứa hủy/xóa upstream.
- E2E recommendation gộp vào `real-capture.spec.ts` để kiểm liên tục cùng các server IDs, thay vì bootstrap thêm một suite/fixture trùng.

### Chu trình đỏ/xanh và phát hiện runtime

| Nhóm | Bằng chứng |
|---|---|
| Transport/metadata | RED 2 suites thiếu module; GREEN các tests JSON/multipart/204/custom prefix/timeout/error envelopes/namespace/stable operation/late epoch; mode sai không fallback |
| Capture mapper/guards | RED 2 tests còn stub; GREEN alias-before-filter, preserve fields, lost 201/202, double create, stable commit replay, metadata write failure, S3 không Bearer/renew 403 |
| Recommendation | RED 1 test còn stub; GREEN wrapper/key/snapshot/PUT/reload; bổ sung dress+shoes RED 1 failed/1 passed rồi sửa CTA real, không đổi quy tắc demo |
| F02/F08 native host | BLOCKED: thiếu `libnspr4.so`; 3 tests không mở được browser, không coi là app RED/PASS |
| F02/F08 container | Image Playwright 1.64.1/Node 24.22.1 có dependencies; RED 3 app failures: fresh/stale editor thành “Món đồ mới”, double submit sinh 2 drafts. GREEN 6/6 mobile+desktop, không reload để che F02 |
| Real auth E2E đầu | 4 fail do accessible password label gồm helper text; sửa accessible name/description. Late-response test được đồng bộ đúng lúc request A đã đi ra trước logout, tránh inject nhầm fixture A vào request B |
| Real capture E2E | Bắt bug transport coi JSON `null` consent là parse failure. Unit RED 1 failed/5 passed; sửa phân biệt JSON null với non-JSON. Sau đó real flow pass, không dùng fixture để che lỗi HTTP |

FE cuối: `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`, `git diff --check` đều exit 0; **7 files / 69 unit tests**. Vite có advisory dynamic/static import cùng client, không ảnh hưởng build. Không chuyển package manager/lockfile.

## T09 — bằng chứng runtime và test profile

### Cách tái lập đã dùng

BE cwd `garblo-backend/`, Python/Ruff từ `/tmp/opencode/garblo-venv/bin/`:

```bash
python -m pytest -q -m 'not integration' --tb=short
ruff check src tests migrations
ruff format --check src tests migrations
bash local-wsl/tests/test-scripts.sh
```

Kết quả cuối: **391 passed / 19 deselected**, exit 0 (184,78s khi chạy song song browser/build); Ruff exit 0, 72 files formatted; CLI-double 14 passed. Một warning passlib/crypt deprecated, không đổi thuật toán/hash ngoài scope.

Integration trên base test stack **không key**:

```bash
GARBLO_TEST_DATABASE_URL=postgresql+psycopg://garblo:garblo-test-only@127.0.0.1:25440/garblo_test \
GARBLO_TEST_S3_ENDPOINT_URL=http://localhost:19001 \
GARBLO_TEST_S3_ACCESS_KEY=garblo-test \
GARBLO_TEST_S3_SECRET_KEY=garblo-test-only-storage-secret \
GARBLO_TEST_API_URL=http://localhost:18081 \
/tmp/opencode/garblo-venv/bin/python -m pytest -q -m integration --tb=short
```

Exit 0: **19 passed / 391 deselected**, 67,47s. Migration thật qua Alembic trên schema UUID trong DB `_test`; concurrent upload/commit, lease/fencing và legacy data được giữ. S3 thật: bucket UUID private, bytes/signed GET, CORS origin, URL TTL=1s hết hạn trả 403 (chờ 2s có chủ ý). API HTTP no-key thực sự từ chối trước enqueue; recommendation không cần AI key. Không dùng SQLite create_all để nhận migration-pass.

### Browser

FE cwd `garblo-frontend/`; image QA riêng build bằng `tests/Dockerfile.e2e`. Không cài OS packages lên host:

```bash
docker build -f tests/Dockerfile.e2e -t garblo-local-wsl-test-e2e:local .
docker run --rm --network host garblo-local-wsl-test-e2e:local npm run test:e2e
docker run --rm --network host \
  -e GARBLO_E2E_BASE_URL=http://localhost:5183 \
  -e GARBLO_CONTROLLED_E2E=1 \
  garblo-local-wsl-test-e2e:local \
  npx playwright test --config playwright.real.config.ts
```

- Demo: **64 passed**, exit 0 (rerun bàn giao source cuối **4,0 phút**), gồm 6 F02/F08 regressions. Config demo không collect các real suites.
- Real: **8 passed**, exit 0 (rerun bàn giao source cuối **1,3 phút**, sau sửa lọc no_items). API/DB/storage/worker process thật, **external inference MOCKED**. Không có skipped được tính pass.
- Real suite kiểm register/login/re-auth sau refresh; không mở demo DB; route Impact bị guard; request A về muộn không hydrate B; synchronous double upload; 3 candidates → SPA Back/resume → edit giữ metadata → commit → wardrobe qua re-auth; ảnh load thật qua CORS; composition snapshot giữ tên cũ dù PATCH wardrobe sau lúc phối; latest like qua reload; consent có thể revoke qua UI; no_items/failed/retry cap không tạo garment giả.
- `compose.controlled.yml` chỉ mount `tests/controlled_runtime.py`, APP_ENV=test + DB `_test`, registry không gọi SDK. Marker `/__controlled-test` chỉ được thêm trong harness; browser từ chối upload/inference nếu marker không khớp, không chỉ tin flag client.
- Model thực trong mock process là `controlled-vision-test`; bảng jobs/ledger ghi đúng test model. `garblo-worker` bình thường đã kiểm idle/heartbeat; claim→publish ở container controlled dùng cùng JobWorker nhưng test registry/loop riêng. Đây không phải bằng chứng production provider wire-live.

### Secret và phạm vi tài nguyên

- FE host build với canary server env: exit 0, không có canary trong **41 artifacts**. Copy bundle từ FE container: **42 files**, không có server dev secrets/canary đã kiểm. Chỉ đọc/public bundle, không lấy token runtime.
- Scan định dạng key trên changed/untracked files: BE 49, FE 41 tại lúc đo, exit 0; không đọc `.env` runtime bị ignore và không in giá trị match/secret. Kiểm source `.dockerignore`/public allowlist; unit test khẳng định không Bearer sang storage. Scan này không thay thế secret audit mọi dữ liệu lịch sử.
- `npm ci` trong image báo **12 moderate vulnerabilities** của dependency tree hiện hữu. Không chạy audit fix/force hay đổi lockfile ngoài scope; DEFERRED việc cập nhật dependencies có kiểm chứng riêng.
- Một traceback pytest RED ban đầu từng in repr chứa JWT của fixture SQLite bộ nhớ (DB đã dispose), không phải token runtime. Không dùng log RED đó làm artifact bàn giao hoặc trích token vào tài liệu. Thêm `addopts = "--tb=short"` để giảm nguy cơ lộ repr fixture khi test lỗi; không thay thế redaction và không bảo đảm mọi assertion/log đều an toàn.
- `docker stats --filter` từng exit 125, đã sửa cách chọn container IDs; không dùng kết quả lỗi làm số đo.
- Chỉ tạo/dừng test project và container PG gate do lượt này tạo. PG gate `garblo-local-wsl-test-postgres-gate` đã stop, volume còn. Không stop/reset stack khác, không `prune`, không `down -v`. Runtime canonical project chưa được start bằng key thật của operator.
- Lifecycle digest so toàn bộ rows media_assets/garments/recommendation_sessions/recommendation_feedback và bytes ảnh trước/sau restart; IDs được so trong harness, không công bố raw user/job/asset IDs hoặc signed URL vào report.

## T10 — live và bàn giao

- **NOT TESTED** OpenAI image analysis live, FPT wire-live và chuỗi wardrobe/composition từ dữ liệu đã đi qua live. Không có ảnh được cấp phép, key/model access và trần request/USD do operator duyệt trong lượt này; số provider live calls do agent thực hiện: **0**.
- `test-live.sh` là entrypoint opt-in, không normal startup; yêu cầu quyền ảnh, image path, account, max requests/USD. Kiểm container/profile thật, trần conservative theo model đã đối chiếu giá; không nhận controlled/test profile, không thêm stages/model/fallback ngoài ngân sách đã chốt. Guard tests từ chối trước Docker khi thiếu opt-in hoặc chọn controlled project.
- Smoke không auto commit thay người dùng: sau phân tích phải review/edit/chọn/commit trên FE, rồi nghiệm thu recommendation/feedback/persistence live riêng. Entry point live chưa chạy phần inference; không dùng guard tests để tick live-pass.
- Tài liệu vận hành: `HUONG_DAN_CHAY_LOCAL_WSL.md`; contract FE đã sửa tài liệu lỗi thời; ba docs local/README/AGENTS phản ánh giới hạn thật. Không commit/push. Hai dòng README FE có trước vẫn nguyên văn.

## Những mục chưa được chứng minh — không đánh dấu DONE toàn hệ thống

| Hạng mục | Trạng thái / bước tiếp |
|---|---|
| Copy sample + chỉ key OpenAI thật trong normal profile, account model access và vision smoke | NOT TESTED: operator cung cấp điều kiện live, chạy entrypoint/runbook |
| FPT wire-live | NOT TESTED, cần duyệt riêng; fallback policy đã có mock regressions |
| Kill worker giữa upstream/publish rồi browser resume, real migration failure/DB-storage outage/readiness timeout | NOT TESTED bằng fault injection Docker; CLI-double/PG fencing/unit readiness đã pass nhưng không thay thế các fault này |
| Lost 201/202 rồi refresh browser/reselect đúng file, partial PATCH/partial commit, URL/JWT hết hạn và recommendation lost-response/repeat/flip trong browser | Chưa đủ browser fault matrix; unit lost-response/selection/diff + PostgreSQL race + S3 TTL thật đã pass riêng |
| Project vật lý nằm dưới path có khoảng trắng, port ngoại xung đột Docker thực | CLI-double pass, lifecycle cwd có khoảng trắng pass; chưa chuyển cả hai repo/chạy full physical-path/conflict test |
| Multi-client candidate CAS, feedback gắn revision, revoke tuyến tính/no-publish/cancel upstream | DEFERRED hoặc ngoài cam kết slice; UI/doc có giới hạn, không tự hứa mạnh hơn |
| Wear/Impact real, generated/VTON/chat, Safari/Windows browser thật | DEFERRED/NOT TESTED đúng scope; không dựng chức năng giả |

## Nhóm file bàn giao

| Repo / nhóm | File chính |
|---|---|
| BE: auth/consent | security.py, schemas.py, consent.py, api/routes.py, main.py, test_local_auth_consent.py |
| BE: durable upload | uploads.py, models.py, migration 0005, test_upload_recovery.py và PostgreSQL integration |
| BE: provider policy | jobs.py, worker.py, catalogue/fallback.py, catalogue/budget.py, ai_providers/contracts.py/openai_direct.py, test_catalogue_fallback.py |
| BE: runtime | local_runtime.py, storage_init.py, config.py, Dockerfile/.dockerignore; toàn bộ local-wsl env/Compose/scripts/test harness |
| FE: HTTP/auth | config.ts, http/client.ts/dto.ts/mappers.ts/metadata.ts/index.ts; features/auth; data/contracts/types.ts, domain.ts/main.tsx |
| FE: slice/gates | capture/review/wardrobe/context/session/board/media và Router/Shell; Dockerfile/.dockerignore/nginx; unit/E2E configs/tests |
| Docs | Kế hoạch/runbook/report local, api-rebuild, backend-integration, hai README/AGENTS; không đổi npm lockfile, không sửa migration 0001 đã phát hành |

## Chốt kiểm tra bàn giao ngày 02/11/2026

- BE cwd `garblo-backend/`: `/tmp/opencode/garblo-venv/bin/python -m pytest -q -m 'not integration'` dùng mặc định `--tb=short` mới, exit 0, **391 passed / 19 deselected**, 65,84s, một warning passlib/crypt. Ruff check/format exit 0, 72 files; `bash -n` năm scripts exit 0; CLI-double **14 passed**, exit 0 (3,663s).
- FE cwd `garblo-frontend/`: typecheck/lint/unit/build exit 0, **69 passed / 7 files**; vẫn có advisory dynamic/static import nêu trên.
- cwd `local-wsl/`: `docker compose --project-directory "$PWD" --project-name <project> --env-file <sample hoặc test env> -f deploy/compose.local.yml [-f <test override>] config --quiet`, exit 0 cho sample thường, test và controlled. `GARBLO_ENV_FILE` trỏ absolute path tương ứng, không dùng/in secret runtime.
- Kiểm `docker ps` theo project label: test stack không còn chạy; PostgreSQL/RustFS named volumes còn. Links tương đối trong ba docs local tồn tại. `git diff --check` cả hai repo exit 0; không stage/commit/push. Hai dòng README FE có trước được giữ.

| DoD / goal | Trạng thái bàn giao |
|---|---|
| G01 normal key-only bootstrap | **NOT TESTED** với key/model thật; sample/config/scripts/build đã kiểm riêng |
| G01/G06 start/stop/persistence | **PASS** trong test project; physical workspace path có khoảng trắng và runtime fault injection còn mở |
| G02 recovery/IDs | **PASS** unit/PG và browser happy path; **NOT TESTED** đầy đủ lost-response + browser refresh/reselect |
| G03 auth/consent/ownership | **PASS** trong phạm vi unit/real E2E/scan đã nêu; JWT expiry browser và audit mọi log lịch sử chưa đạt |
| G04 real/error semantics | **PASS** luồng thực với inference mock; partial/network/media fault browser chưa đủ |
| G05 provider policy/budget | **PASS** offline/wire-mock; OpenAI/FPT live **NOT TESTED**, số calls live của agent **0** |
| G06 F02/F08/metadata/ảnh | **PASS** red→green, re-auth và lifecycle; không tuyên bố chống mọi multi-writer |
| G07 composition/feedback/scope | **PASS** DB + browser snapshots/feedback; wear/generated/VTON real **DEFERRED** |
| G08 evidence/docs/runtime | **PASS** cho ba tài liệu và runtime test riêng; không suy thành AI live hoặc toàn bộ fault matrix |

## Cách chạy lại kiểm tra backend

Từ `garblo-backend/`, dùng `/tmp/opencode/garblo-venv/bin/python` và `/tmp/opencode/garblo-venv/bin/ruff`. Nếu venv không còn, tạo lại và cài `.[dev]` như T00. Integration chỉ dùng DB `_test` và bucket riêng; không tự sử dụng `.env` hay key runtime.
