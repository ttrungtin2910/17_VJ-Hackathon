# Garblo local WSL — Kế hoạch triển khai FE–BE

> **Hướng dẫn thực thi:** Làm từng task theo checkbox, kiểm chứng trước khi chuyển gate. Dùng các skill đang có ở mục 3; không tự gọi `superpowers:*` hoặc tự giao việc cho subagent vì các skill đó chưa được công bố trong phiên. Tài liệu này là kế hoạch, không phải bằng chứng đã triển khai.

**Goal:** Người phát triển chỉ cần copy `.env.sample`, nhập OpenAI key và chạy `./start-all.sh` để có hệ FE–BE thật trên WSL; upload → nhận diện → review → commit → wardrobe → composition → feedback, giữ DB/ảnh sau refresh và stop/start.

**Architecture:** Giữ hai repo cạnh nhau, dùng một Docker Compose quản lý FE static, FastAPI, worker, PostgreSQL và RustFS. Tái sử dụng hàng đợi PostgreSQL, migrations, storage và provider wrappers; chỉ bổ sung contract recovery còn thiếu. OpenAI chính, FPT chỉ dự phòng phân tích ảnh với chính sách retry được snapshot và giới hạn ngân sách.

**Tech Stack:** React 19, TypeScript, Vite, TanStack Query, npm/Node >=24; Python >=3.12, FastAPI, SQLAlchemy, Alembic; PostgreSQL, RustFS, Docker Compose, Bash, Vitest, Playwright, pytest, Ruff.

**Spec:** [task-connect-FE-BE.md](../../task-connect-FE-BE.md). Đọc cùng [AGENTS backend](../AGENTS.md), [AGENTS frontend](../../garblo-frontend/AGENTS.md) và [blueprint audit](../../docs/audits/garblo-fe-be-integration/2026-10-30T22-09-41Z/06_FE_BE_INTEGRATION_BLUEPRINT.md).

**Ngày lập:** 01/11/2026. **Cập nhật:** 02/11/2026. **Trạng thái:** Đã triển khai lõi T00–T08; T09 đã có bằng chứng offline/integration/browser/lifecycle nhưng còn fault matrix; T10 đã có tài liệu và live guard, AI live **NOT TESTED**. Xem [báo cáo](BAO_CAO_TRIEN_KHAI_LOCAL.md) và [runbook](HUONG_DAN_CHAY_LOCAL_WSL.md). Chưa toàn bộ DONE/live-ready; không tự commit hoặc gọi provider trả phí.

## 1. Ràng buộc toàn cục

- Toàn bộ trao đổi, tài liệu, chú thích mới và UI bằng tiếng Việt; giữ nguyên identifiers/API đang dùng tiếng Anh.
- Chỉ WSL2 Ubuntu + Docker Engine/Docker Desktop WSL integration + Bash; không AWS, PowerShell, CI/CD hoặc production HTTPS.
- Hai repo riêng: chạy Git/test đúng repo; không reset, checkout đè, xóa hoặc stage thay đổi có trước. Không tự commit/push nếu chưa được yêu cầu.
- Không viết lại FE/BE, không đổi agent framework, không thêm Redis/Celery/vector DB. Giữ RustFS thay vì đổi storage không có lý do.
- Chế độ real không gọi DemoDataSource, không seed đồ giả, không âm thầm fallback IndexedDB demo khi backend/provider lỗi.
- Không gửi provider/DB/JWT keys xuống browser; mọi `VITE_*` đều là public. Không log ảnh, password, token, raw provider body hoặc signed URL.
- Bình thường chỉ `docker compose down` không kèm `-v`; cấm `rm -rf`, `killall`, `docker system prune` và `pkill -f` không ràng buộc process trong start/stop.
- Auth, consent, ownership, worker lease/fencing, ID server và tổng retry/token/call budget phải được giữ qua mọi thay đổi.
- Chỉ gọi provider live sau khi có ảnh được cấp phép và trần request/chi phí được operator duyệt; thiếu điều kiện thì ghi `NOT TESTED`.
- Ghi riêng `PASS / FAIL / BLOCKED / NOT TESTED / DEFERRED`; test mock/demo không chứng minh AI live hoặc PostgreSQL migration hoạt động.

## 2. Mục tiêu phải giữ — keep goals

| ID | Mục tiêu bất biến | Bằng chứng cuối cùng |
|---|---|---|
| G01 | Copy env + chỉ nhập OpenAI key + hai lệnh start/stop đủ vận hành | Bootstrap trên cấu hình mới; FPT để trống; không sửa secret/connection string khác |
| G02 | Một thao tác logic giữ đúng asset/job/candidate/garment/session/outfit IDs | Lost-response, refresh, replay và concurrency tests; không chọn “job mới nhất” |
| G03 | Người dùng kiểm soát dữ liệu, consent và quyền sở hữu | Hai user cách ly; logout/401 xóa cache; không inference khi consent không hợp lệ |
| G04 | Lỗi và kết quả hiển thị trung thực, không demo trá hình | Real-mode negative E2E; empty/no_items/failed phân biệt rõ; không “đã lưu” trước thành công |
| G05 | OpenAI chính, FPT fallback vision có giới hạn, không vượt quyền gọi | Provider policy tests + audit từng physical call; live chỉ trong budget được duyệt |
| G06 | Refresh/restart không mất DB/ảnh hoặc ghi đè metadata đã lưu | Persistence E2E, F02 regression, start/stop lặp, named volumes giữ nguyên |
| G07 | Thay đổi tối thiểu, bảo vệ việc sẵn có và đúng scope local | Git diff từng repo; không đụng container dự án khác; không generated lookbook/VTON/chat/wear real |
| G08 | Kết luận dựa trên bằng chứng tái lập, không chỉ source hoặc suite cũ | Lệnh, cwd, exit status, counts, IDs đã redacted và ba tài liệu tiếng Việt |

### Năm tình huống cần review đặc biệt

1. Mất response `201/202` rồi refresh: phải tìm lại đúng operation, không tạo upload/job thứ hai — T02, T07, T09.
2. Saved → Back → resume SPA và response về đảo thứ tự: editor không ghi đè field mới bằng snapshot cũ — T07.
3. Logout user A, login user B khi request A chưa trả: không rò cache, ảnh hoặc operation — T06, T09.
4. Startup đồng thời, path có khoảng trắng, port do stack khác chiếm: không double migration/process, không giết stack khác — T05, T09.
5. Consent bị rút hoặc provider trả auth/safety/billing error: không đổi provider để lách lỗi, không báo success — T01, T03, T09.

## 3. Dùng skill nào, vào lúc nào

| Skill | Dùng cho | Cách áp dụng / giới hạn |
|---|---|---|
| `writing-plans` | T00 và cập nhật kế hoạch sau phát hiện mới | Task nhỏ, file/interface/test rõ; tự review spec coverage, không chỉ liệt kê công việc chung chung |
| `docker-compose-patterns` | T04, T05, T09 | Health dependencies, volume bền, internal/browser URLs, env và vòng đời; dùng `config --quiet` để tránh in secrets |
| `ui-ux-pro-max` | T06, T07, T08, T09 | Auth/consent, pending/retry/error, keyboard/focus, mobile và thông báo đúng nơi lưu dữ liệu; giữ semantic tokens hiện có |
| `verification-before-completion` | Gate của tất cả T00–T10 | Chạy lệnh mới, đọc đầy đủ kết quả/exit code trước khi đánh dấu hoàn thành; không suy lint pass thành build/live pass |
| `frontend-design` | Chỉ khi T06 cần màn auth hoặc thay layout đáng kể | Bám visual identity Garblo hiện có, không redesign hoặc mở rộng sản phẩm ngoài scope |

Thư viện đã kiểm: `~/.agents/skills/` có planning/Compose/verification; `~/.config/opencode/skills/` có frontend-design/ui-ux-pro-max/xlsx; `.agents/skills/` của workspace hiện trống. `xlsx`, `opencode`, `report` không cần cho task này. Các skill Docker/superpowers được tham chiếu trong nội dung skill nhưng không có trong danh sách hiện tại không phải dependency được tự ý gọi/cài.

Ưu tiên spec và AGENTS hơn ví dụ chung của skill: giữ tên `compose.local.yml`, không copy Redis từ template, không tự tạo worktree/subagent, không tự viết thêm plan ngoài file này.

## 4. AS-IS và mức kiểm chứng

### Snapshot source đã kiểm tra lại khi tạo tài liệu

| Repo | Branch / HEAD | Working tree trước khi tạo plan |
|---|---|---|
| `garblo-frontend/` | `main` / `7e178e3` | `README.md` có hai dòng thêm sẵn; phải giữ nguyên |
| `garblo-backend/` | `rebuild/openai-fpt-ai-factory` / `4e7a1b8` | Sạch |

- FE: `src/data/adapters/http/index.ts` có 19 stub methods; mọi method báo chưa sẵn sàng, chưa có auth/HTTP thật.
- BE: 32 operations `/v1` được mount qua `main.py`; catalogue/review/commit/garments, recommendation composition và feedback đã có trong source.
- Worker riêng `garblo-worker`, bảng `ai_jobs`, `FOR UPDATE SKIP LOCKED`, lease và fencing; không cần message broker mới.
- Storage abstraction và Compose hiện tại dùng private RustFS/S3, có internal/public endpoint riêng.
- Catalogue không tự tạo garment; người dùng phải explicit commit. Commit cùng candidate trả lại ID cũ.
- Recommendation có context version 1 khớp FE, tối đa 3 outfit, template áo+quần+giày hoặc váy liền+giày; feedback PUT là latest signal.
- FPT hiện chỉ explicit `vlm_provider=fpt`, chưa có fallback từ OpenAI. Job/provider/model snapshot cũ phải giữ nguyên.
- `/health` báo config/status, chưa phải readiness DB/storage; worker chưa có readiness heartbeat.
- `garblo-frontend/docs/backend-integration.md` lỗi thời; đối chiếu routes/schema thay vì coi tài liệu này là contract chuẩn.
- Review cache/editor và capture guard hiện chưa xử lý triệt để F02/F08; wear/Impact còn được mount trong real dù BE chưa có API tương ứng.

### Bằng chứng baseline từ lượt audit trong cùng hội thoại

| Kiểm tra đã chạy | Kết quả / phạm vi |
|---|---|
| FE `npm exec -- tsc --project tsconfig.app.json --noEmit`, tương tự `tsconfig.node.json`; `npm run lint` | Exit 0; chỉ typecheck/lint |
| FE `npm exec -- vitest run --no-cache --configLoader runner` | 3 files, 51 tests passed, exit 0; demo/unit, không AI thật |
| BE `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider -m 'not integration'` | Exit 1, host thiếu `pytest`; chưa chạy test. Host cũng thiếu `pip` |
| Docker CLI/Compose/daemon, WSL, cổng | Hoạt động ở lượt audit; `8000` và `5432` đang thuộc stack khác |
| Build, browser E2E, PostgreSQL migration, storage/worker runtime, provider live | Chưa chạy lại trong phiên này; không dùng report cũ để đánh dấu PASS |

Các kết quả trên là baseline có giới hạn, không được tái sử dụng làm nghiệm thu sau sửa code. T00/T09 phải kiểm tra mới. Môi trường đang có nhiều container khác; RAM trống và cổng phải đo lại trước khi start, không tự dừng container để giải phóng tài nguyên.

## 5. TO-BE tối thiểu và nơi quản lý version

```text
Browser → localhost:5173  → FE static / SPA fallback
        → localhost:18080 → FastAPI /v1 → postgres:5432
                                      → rustfs:9000
        → localhost:19000 → RustFS signed media URL
Worker → PostgreSQL queue → OpenAI → FPT chỉ khi policy fallback cho phép
```

- Publish FE/API/storage trên `127.0.0.1`; PostgreSQL internal-only ở cấu hình thường.
- Giữ PostgreSQL/RustFS named volumes; project ổn định `garblo-local-wsl`, test project `garblo-local-wsl-test` dùng volumes/ports khác. Không hardcode tên volume dùng chung qua hai project.
- FE build bằng Node >=24, phục vụ static trong container; không quản lý node PID ngoài Docker.
- FE build context chỉ là repo FE; chỉ truyền allowlist biến public. Không mount `.env` điều phối vào container FE.
- `VITE_DATA_MODE=real`, `VITE_API_BASE_URL=http://localhost:18080/v1`; API CORS phải nhận đúng localhost/127.0.0.1 của FE. Không nối `/v1` hai lần.
- Signed URL dùng public RustFS endpoint; API/worker truy cập internal endpoint. Blob fetch cần bucket CORS, không chỉ API CORS.
- Readiness không gọi provider trả phí. Capability/model-access verification và AI live là gate riêng.

Workspace root không phải Git repo. Đặt bộ điều phối canonical ở `garblo-backend/local-wsl/`, docs trong `garblo-backend/docs/`; Compose trỏ repo FE cạnh backend, không copy source, không khởi tạo Git repo ở root. Đây là điều chỉnh layout so với ví dụ root trong spec để mọi artifact triển khai đều được version. Spec gốc ở workspace được tham chiếu; phần contract cần thiết được ghi ngay trong plan/API docs để người thực thi không phụ thuộc vào chat.

Lệnh vận hành đã triển khai (normal profile với key thật chưa nghiệm thu):

```bash
cd /duong-dan-workspace/garblo-backend/local-wsl
cp .env.sample .env
# Chỉ nhập OPENAI_API_KEY; FPT_VLM_API_KEY là tùy chọn.
./start-all.sh
./stop-all.sh
```

`start-all.sh`/`stop-all.sh` phải chạy được bằng absolute path từ cwd bất kỳ. Giữ `garblo-backend/compose.yaml` và `.env.example` hiện tại hoạt động độc lập; env điều phối mới không ghi đè env sẵn có của repo.

## 6. Map đủ 19 methods của HttpDataSource

Mọi route trong bảng có prefix `/v1`; ngoài login/register đều dùng Bearer. Adapter đã nối theo bảng disposition dưới đây; phạm vi kiểm chứng real/mock/live xem báo cáo.

| # | Method | Contract / disposition |
|---|---|---|
| 1 | `createCapture(file, consent)` | Grant consent khi người dùng đồng ý → multipart `POST /assets` (`file`, `asset_type=garment_photo`) → JSON `POST /assets/{id}/catalogize` → giữ asset/job IDs |
| 2 | `getCapture(draftId)` | `draftId=asset_id`; metadata asset, lookup job đúng asset, `GET /jobs/{id}`, `GET /assets/{id}/candidates`; map running→processing, succeeded/no_items riêng |
| 3 | `listCaptures()` | `GET /assets`, chỉ garment_photo và nháp chưa hoàn tất; phục hồi job bằng source lookup, không chọn job mới nhất toàn hệ |
| 4 | `updateCapture(draftId, candidates)` | Diff editable fields rồi `PATCH /candidates/{id}`; name→details.display_name, selected→draft/rejected; không gửi nguyên domain array |
| 5 | `commitCapture(draftId)` | `POST /assets/{id}/commit` với `{candidate_ids}` sau flush; response `{garment_ids}`→`{itemIds}`; giữ selection của attempt khi replay |
| 6 | `listWardrobe(category?)` | `GET /garments`; normalize alias category trước filter FE; không query `category=top` rồi làm mất backend `shirt`; thống nhất active policy |
| 7 | `getWardrobeItem(itemId)` | `GET /garments/{id}`, dùng chung mapper list/detail |
| 8 | `getMedia(mediaId)` | `GET /assets/{id}/download`→`{url, expires_in_seconds}`→fetch Blob, không gửi Bearer sang S3; renew URL tối đa một lần khi expiry/403 |
| 9 | `getLookbookContextDraft()` | Local-only metadata real theo API root/user; không dùng demo DB hoặc bịa HTTP route |
| 10 | `saveLookbookContextDraft(draft)` | Persist nháp chưa hợp lệ/ô Khác trong real metadata store; công khai chỉ lưu trên thiết bị |
| 11 | `createLookbookSession(context)` | JSON `POST /recommendations` với `{context}` và `Idempotency-Key`; map full SessionResponse thành `{sessionId}` và cache canonical |
| 12 | `getLookbookSession(sessionId)` | `GET /recommendations/{id}`; giữ context, per-outfit item snapshots, revision, feedback và lý do thiếu đồ |
| 13 | `listLookbookSessions()` | `GET /recommendations?limit=...&offset=...`; giữ pagination/order, không gọi legacy `/lookbooks` |
| 14 | `submitFeedback(sessionId, outfitId, decision)` | `PUT /recommendations/{session}/outfits/{outfit}/feedback` với `{signal: decision}`; không đổi thành approval hoặc wear |
| 15 | `listWearEvents()` | `DEFERRED` trong real; lỗi `FEATURE_UNAVAILABLE`, không trả `[]` giả thành dữ liệu server |
| 16 | `recordWearEvent(input)` | `DEFERRED` trong real; không ghi local thay DB |
| 17 | `seedSamples()` | `DEMO_ONLY`; không được gọi trong real |
| 18 | `removeSamples()` | `DEMO_ONLY`; không tạo API xóa dữ liệu thật cho demo tools |
| 19 | `resetDemo()` | `DEMO_ONLY`; không dùng để reset real volumes/DB |

## 7. Task, skill, đầu ra và keep goal

| Task | Hoàn thành việc gì | Skill chính | Keep goal | Phụ thuộc |
|---|---|---|---|---|
| T00 | Chốt source/config/test baseline mới và môi trường kiểm tra | writing-plans + verification-before-completion | G07, G08 | Không |
| T01 | Auth/password policy và consent recovery an toàn | ui-ux-pro-max khi rà consent copy + verification-before-completion | G03, G04 | T00 |
| T02 | Upload idempotency và lookup chính xác asset/job | verification-before-completion | G02, G03, G06 | T00 |
| T03 | Fallback vision có snapshot/audit/budget, giữ legacy | verification-before-completion | G03, G04, G05 | T00, T01 |
| T04 | Compose/env/readiness và build FE container | docker-compose-patterns + verification-before-completion | G01, G03, G06, G07 | T01–T03 |
| T05 | Start/stop idempotent, không phá dữ liệu | docker-compose-patterns + verification-before-completion | G01, G06, G07 | T04 |
| T06 | HTTP transport/auth/cache boundary và real metadata store | ui-ux-pro-max + verification-before-completion | G02, G03, G04 | T01, T02 |
| T07 | Capture/review/commit/wardrobe thật; sửa F02/F08 | ui-ux-pro-max + verification-before-completion | G02, G04, G06 | T02, T06 |
| T08 | Context/composition/feedback thật; gate wear/Impact | ui-ux-pro-max + verification-before-completion | G02, G04, G07 | T06, T07 |
| T09 | Regression, integration/runtime, lifecycle và secret checks | docker-compose-patterns + ui-ux-pro-max + verification-before-completion | G01–G08 | T03–T08 |
| T10 | Live nghiệm thu có phép và tài liệu bàn giao | verification-before-completion | G01, G05, G08 | T09 |

Các nhánh có thể chuẩn bị độc lập theo dependency nhưng mặc định thực hiện trong cùng phiên, không tự spawn agent. Mỗi task dùng vòng test đỏ → code tối thiểu → test xanh → diff review; checkbox phản ánh bằng chứng trong báo cáo, không suy phần chưa tick thành đã đạt.

### T00 — Baseline và chuẩn bị môi trường kiểm tra

**Files:** Đọc `garblo-frontend/{AGENTS.md,package.json,playwright.config.ts}`, `garblo-backend/{AGENTS.md,pyproject.toml,compose.yaml,Dockerfile}`; cập nhật kết quả vào `garblo-backend/docs/BAO_CAO_TRIEN_KHAI_LOCAL.md` khi bắt đầu triển khai.

**Interface/đầu ra:** Danh sách branch/HEAD/dirty files, phiên bản runtime, Docker availability, cổng, baseline commands/counts; một Python >=3.12 test environment có pytest/Ruff, không cài đè system Python.

- [x] Kiểm lại status cả hai repo và diff README FE; giữ nguyên thay đổi sẵn có.
- [x] Đo Docker/Compose, ports, RAM/disk; không dùng cổng 8000/5432 đang bị chiếm và không tự dừng container khác.
- [x] Chuẩn bị venv dưới `/tmp/opencode/garblo-venv` hoặc test image riêng nếu host không đủ công cụ; ghi rõ cách chạy lặp lại, không giả venv audit cũ còn tồn tại.
- [x] Chạy FE typecheck/lint/unit/build và BE offline/Ruff theo mục 8; lưu exit code/counts/redacted log. Nếu dependency install bị chặn, ghi BLOCKED và tiếp tục phần kiểm tra khả thi.

**Gate:** Baseline mới có kết quả hoặc blocker cụ thể cho từng nhóm, không chép số pass từ spec.

### T01 — Auth và consent có thể phục hồi

**Files:** Sửa `garblo-backend/src/garblo/{security.py,schemas.py,consent.py,api/routes.py}`; thêm `garblo-backend/tests/test_local_auth_consent.py`; cập nhật `garblo-backend/docs/api-rebuild.md`.

**Interface/đầu ra:** Giữ JSON register/login và Bearer hiện hữu. Thêm `GET /v1/me/consents/latest?purpose=ai_processing` trả `ConsentResponse | null`, owner-scoped, gồm cả latest record đã revoked; giữ DELETE consent trả 204. Đây là route mới đề xuất phải được code/test trước khi FE dùng.

- [x] Viết/chạy test đỏ: `test_password_utf8_over_72_bytes_is_rejected`, `test_legacy_login_different_suffix_is_not_accepted`, `test_latest_consent_is_owned_and_does_not_revive_old_grant`, `test_revoke_is_repeatable`.
- [x] Áp dụng giới hạn bcrypt 72 UTF-8 bytes phía server: register quá dài trả 422; login quá dài không verify với prefix bị cắt, trả lỗi auth an toàn; không sửa/xóa hash user cũ hoặc tự đổi thuật toán.
- [x] Thêm consent latest lookup; dùng consent backend thật, không tự grant khi bootstrap/login. Policy `local-wsl-1` là nhãn local được mô tả rõ, không tự nhận là policy production đã phê duyệt.
- [x] Chạy `python -m pytest -q tests/test_local_auth_consent.py tests/test_auth_and_ai_unavailable.py` và review API docs/copy về quyền xử lý ảnh.

**Gate:** Password suffix không bị bỏ qua; refresh có thể đọc/revoke đúng consent. Giữ đúng giới hạn hiện hữu: checkpoint consent không có nghĩa đã hủy request upstream, xóa dữ liệu provider hoặc tuyến tính hóa revoke với publish; không đưa cam kết mạnh hơn vào UI.

### T02 — Durable upload và source→job recovery

**Files:** Thêm `garblo-backend/src/garblo/uploads.py`, `garblo-backend/migrations/versions/20261101_0005_upload_operations.py`, `garblo-backend/tests/test_upload_recovery.py`; sửa `garblo-backend/src/garblo/{models.py,schemas.py,api/routes.py}`, `garblo-backend/tests/test_postgres_integration.py` và `garblo-backend/docs/api-rebuild.md`.

**Interface/đầu ra:** `POST /assets` nhận optional `Idempotency-Key` để giữ tương thích client cũ, FE real luôn gửi. Thêm `GET /assets/by-request/{request_key}` trả `{request_key, status: pending|completed|failed, asset_id: string|null}` và `GET /assets/{asset_id}/jobs` trả catalogue `JobResponse[]` đúng owner/source; thêm `GET /assets/{asset_id}` dùng `AssetResponse` hiện có để deep-link không phải tải toàn bộ assets. Không expose raw `input_ref`.

- [x] Viết/chạy test đỏ: `test_upload_replay_returns_same_asset`, `test_upload_same_key_different_body_conflicts`, `test_upload_lookup_recovers_lost_response`, `test_catalogue_lookup_uses_source_not_latest_job`, `test_foreign_operation_is_not_visible`.
- [x] Thêm operation reservation bền theo `(user_id, request_key)` và request hash từ asset_type + ảnh normalized; lưu object key/phase để retry cùng operation không tạo logical asset thứ hai. Same key/body replay, khác body 409. DB/S3 không atomic: có thể còn orphan object sau crash; không tự xóa dữ liệu để che lỗi.
- [x] Không giữ DB transaction mở xuyên network write. Pending operation có lease/recovery, không lấy key vừa reserve làm bằng chứng upload đã xong. Nếu cần gửi lại bytes sau refresh, yêu cầu chọn lại đúng file và giữ key; không lưu ảnh real vào demo DB.
- [x] Giữ catalogize khóa asset và retry cùng job; source lookup phân biệt chưa enqueue với response 202 bị mất. Nếu có hơn một job catalogue không định danh được, trả lỗi nhất quán thay vì chọn newest.
- [x] Chạy `python -m pytest -q tests/test_upload_recovery.py tests/test_catalogue_draft_commit.py`; thêm race/rollback/migration tests vào PostgreSQL suite; đã chạy sớm với DB test riêng ở T02, T09 cần chạy lại toàn bộ.

**Gate:** Mất 201/202 vẫn tìm đúng asset/job, hai request cùng key chỉ có một logical asset; migration additive và không làm mất garment/asset cũ.

### T03 — OpenAI chính, FPT fallback riêng vision

**Files:** Thêm `garblo-backend/src/garblo/catalogue/fallback.py`, `garblo-backend/tests/test_catalogue_fallback.py`; sửa `garblo-backend/src/garblo/{config.py,jobs.py,worker.py}`, `garblo-backend/src/garblo/catalogue/{stages.py,budget.py}`, `garblo-backend/src/garblo/ai_providers/{contracts.py,openai_direct.py,fpt_qwen.py}` khi cần cho error/audit; cập nhật backend `AGENTS.md` và README về policy mới.

**Interface/đầu ra:** Bổ sung setting `CATALOGUE_FPT_FALLBACK_ENABLED` mặc định false ngoài local profile. Job catalogue OpenAI mới snapshot fallback provider/model, ngưỡng primary attempts và max attempts; không snapshot key. Giữ `job.model_provider` là provider yêu cầu, audit/output ghi riêng effective provider/model đã chạy. `run_analysis_stages(...) -> Analysis` giữ contract/schema hiện hữu.

- [x] Viết/chạy test đỏ: `test_transient_openai_failure_can_use_fpt`, `test_missing_fpt_key_stays_openai_only`, `test_auth_safety_consent_and_invalid_input_never_fallback`, `test_fallback_preserves_budget_and_fencing`, `test_legacy_job_does_not_gain_fallback`.
- [x] Khi local policy bật và có FPT key, dùng tối đa hai worker attempts OpenAI rồi dành attempt thứ ba cho FPT sau lỗi transient liên tiếp; trường hợp max attempts <3 không tự tăng giới hạn để thêm fallback. Khi thiếu FPT key, giữ OpenAI-only trong budget gốc và cảnh báo một lần.
- [x] Phân loại timeout/network/408/429 tạm thời/5xx; không coi billing/config/auth errors là transient chỉ vì status 429. Không fallback do consent, safety/refusal, invalid input/output, mất lease hoặc hết call/token/deadline budget.
- [x] Trước mọi physical call vẫn chạy consent/fencing và CallLedger reservation. Provider schema-repair cũng tiêu budget; không thêm vòng retry ngoài ledger. FPT native structured output tắt nếu adapter không hỗ trợ; validate cùng Pydantic schema sau response.
- [x] Log correlation request/job ID, provider/model, latency, attempt và trạng thái ở dạng đã redacted; không sửa snapshot legacy/explicit-FPT hoặc bật fallback cho render/intent.
- [x] Chạy `python -m pytest -q tests/test_catalogue_fallback.py tests/test_worker_provider_retry.py tests/test_catalogue_v2_provider.py tests/test_catalogue_stages.py`.

**Gate:** Policy bị cấm không gọi FPT; audit/budget phản ánh mọi call; FPT lỗi trả failure hữu ích, không output giả. Live verification vẫn thuộc T10.

### T04 — Compose, env và readiness thật

**Files:** Thêm `garblo-backend/local-wsl/{.env.sample,.gitignore,deploy/compose.local.yml}`, `garblo-backend/src/garblo/local_runtime.py`, `garblo-backend/tests/test_local_readiness.py`, `garblo-frontend/{Dockerfile,.dockerignore,deploy/nginx.conf}`; sửa backend `src/garblo/{main.py,worker.py,storage_init.py,config.py}` khi cần.

**Interface/đầu ra:** `GET /ready` trả 200 khi DB/storage/migration sẵn sàng, 503 nếu thiếu dependency; không trả connection strings. Worker ghi heartbeat riêng ở `/tmp/garblo-worker-heartbeat`, health probe kiểm heartbeat + DB/storage và xét thời gian tối đa job hợp lệ. Giữ `/health` không bị biến thành bằng chứng provider live.

- [x] Viết/chạy test đỏ `test_readiness_requires_db_storage_and_schema`, `test_worker_dead_loop_is_not_ready`, `test_missing_fpt_does_not_block_local_configuration` trong `tests/test_local_readiness.py`.
- [x] Build FE bằng Node >=24 và `npm ci`; dùng static server có SPA fallback. Pin image version/digest đã kiểm, không `latest`. Không mount source/env backend vào FE hoặc đưa secrets vào build args.
- [x] Tạo Compose services `postgres`, `rustfs`, `storage-init`, `migrate`, `api`, `worker`, `web`; condition health/completed rõ ràng. Kiểm image RustFS có công cụ gì trước khi viết probe; nếu thiếu shell/curl, dùng probe bên backend/sidecar thay vì giả lệnh có sẵn.
- [x] `.env.sample` có comment tiếng Việt, dev-only credentials URL-safe và JWT secret đủ dài, ổn định qua restart; không yêu cầu người dùng sửa chúng. Database URL nội bộ trỏ `postgres`, S3 nội bộ trỏ `rustfs`; API public mặc định 18080, FE 5173, media 19000.
- [x] Dùng đúng `OPENAI_API_KEY`, `OPENAI_VLM_MODEL`, `FPT_VLM_API_KEY`, `FPT_VLM_BASE_URL`, `FPT_VLM_MODEL`; FPT source hiện trỏ `https://mkp-api.fptcloud.com/v1`, `Qwen2.5-VL-7B-Instruct`, chưa phải bằng chứng account live. Không đặt `OPENAI_IMAGE_MODEL` làm dependency của composition readiness.
- [x] Chọn model mặc định từ profile đã xác minh tài liệu chính thức về vision/structured schema và wrapper compatibility; ghi model ID/profile vào sample, không để operator phải tự điền model. Kiểm quyền model bằng API không thay thế smoke vision. Không có model khả dụng thì fail rõ, không tự thử hàng loạt model; gate live chưa đạt cho đến T10.
- [x] Cấu hình bucket private/CORS GET, HEAD theo origins FE; API CORS gồm public FE origins. Health probes không gọi paid API; thêm log level có kiểm soát, không bật SDK debug.
- [x] Chạy readiness tests, build từng image, kiểm Compose bằng `config --quiet` theo mục 8; đo RAM/disk thực tế để viết runbook, không hứa stack chạy được chỉ dựa vào container tồn tại.

**Gate:** Sample đủ biến, chỉ OpenAI key phải nhập; FE image không chứa secrets; DB/storage init/migrations/readiness phân biệt được thành công và lỗi.

### T05 — Hai scripts điều phối an toàn

**Files:** Thêm `garblo-backend/local-wsl/{start-all.sh,stop-all.sh}`, `garblo-backend/local-wsl/tests/test-scripts.sh`; cập nhật ignore cho `.env`, lock và dữ liệu/log nhạy cảm tại bộ điều phối.

**Interface/đầu ra:** Hai scripts không cần positional arguments, tự resolve root; mặc định dùng Compose prefix `docker compose --project-name garblo-local-wsl --project-directory <local-wsl> --env-file <local-wsl>/.env -f <local-wsl>/deploy/compose.local.yml` với quote đúng. Test harness được chọn `GARBLO_COMPOSE_PROJECT=garblo-local-wsl-test`, `GARBLO_ENV_FILE` và `GARBLO_COMPOSE_OVERRIDE_FILE` bằng absolute paths; lock scope theo project. `GARBLO_TEST_MODE=1` chỉ cho phép bỏ kiểm key khi project đúng `garblo-local-wsl-test` và đã chọn env/override test riêng không có AI keys; không phải fake-provider flag. Những biến này là cấu hình script, không phải Settings BE hoặc biến Vite. Exit 0 chỉ khi hành động đạt gate; lỗi cấu hình/readiness exit khác 0.

- [x] Viết/chạy test đỏ bằng Docker CLI double trong `tests/test-scripts.sh`: missing env/key, daemon lỗi, port ngoại chiếm, cwd khác, path có khoảng trắng, start đồng thời, timeout và stop trước start; test mode trên project thường phải bị từ chối. Assert lifecycle test không phát lệnh tới project/volume runtime; không cần provider thật.
- [x] Dùng `#!/usr/bin/env bash`, `set -Eeuo pipefail`, lock không gây stale deadlock; chặn `set -x`. Đọc dotenv đúng chuẩn, không `eval` hoặc `source` file dotenv như shell script tùy ý; không in resolved config/keys khi lỗi.
- [x] Start: validate env/Docker/cổng → build → DB/storage healthy → migrations một lần được serialize trong mỗi lượt start → API/worker/web → bounded readiness waits. Start lại không trùng service và không rotate secrets. Port đang do đúng project dùng không bị nhận nhầm là conflict.
- [x] Stop: chỉ project này, dùng `down` không `-v`, chạy lặp được kể cả thiếu key hoặc chưa start; không yêu cầu provider readiness để dừng. Failure khi start giữ dữ liệu và in lệnh log đã quote, không tự prune/rollback DB.
- [x] `chmod +x` scripts; chạy `bash -n local-wsl/start-all.sh local-wsl/stop-all.sh`, `bash local-wsl/tests/test-scripts.sh` từ backend root; ShellCheck nếu có. Ghi cách sửa CRLF bằng `dos2unix` trong runbook.

**Gate:** CLI-double tests xanh; lifecycle thực hai lần start/stop được kiểm riêng ở T09, không dùng double thay bằng chứng Docker thật.

### T06 — HTTP client, auth và real metadata boundary

**Files:** Thêm frontend `src/data/adapters/http/{client.ts,dto.ts,mappers.ts,metadata.ts}`, `src/features/auth/{session.ts,AuthGate.tsx}`, `tests/unit/{http-client.test.ts,real-metadata.test.ts}`, `tests/e2e/real-auth.spec.ts`; sửa frontend `src/{data/index.ts,app/Router.tsx,lib/domain.ts,main.tsx}` và `src/data/adapters/http/index.ts`.

**Interface/đầu ra:** `ApiClient.request<T>(path: string, options?: RequestInit): Promise<T>` dùng API root có prefix, parse/validate DTO trong adapter; `login(email: string, password: string): Promise<void>`, `logout(): void` trong session module, giữ token memory-only và GET `/me` xác nhận identity. Metadata DB `garblo-real-metadata-v1` chỉ lưu operations/context/consent refs theo `{apiRoot,userId}`, không ảnh/token/URL ký. Giữ 19 signatures GarbloDataSource; thêm capability retry riêng ở T07.

- [x] Viết/chạy test đỏ: `http-client.test.ts` kiểm JSON/multipart boundary/204/error envelopes/non-JSON/timeout/custom prefix; `real-metadata.test.ts` kiểm namespace, write failure trước request, stable pending operation key và không truy cập demo DB.
- [x] Thêm auth gate gọn; login/register JSON, labels/autocomplete/password-manager/paste hoạt động, lỗi accessible. Refresh token memory mất thì re-auth và resume đúng user, không tự đăng ký account hoặc grant consent.
- [x] Query keys real chứa mode/API root/user; epoch + abort bảo đảm response user A về muộn không hydrate user B. Logout/401 dừng poll, clear query/media và context; giữ operation refs tối thiểu để cùng user re-auth resume. Không tự replay POST không idempotent sau login.
- [x] Validate `VITE_DATA_MODE`/base URL, invalid mode fail rõ thay vì default demo âm thầm; demo explicit và offline hiện hữu vẫn hoạt động.
- [x] Chạy `npm run test -- tests/unit/http-client.test.ts tests/unit/real-metadata.test.ts`; E2E auth real kiểm re-auth sau refresh, hai user và late response. Frontend-design chỉ dùng khi thật sự cần thiết kế auth UI mới.
- [ ] E2E browser khi JWT thực sự hết hạn theo thời gian (401 có unit coverage, chưa có browser expiry injection).

**Gate:** Transport/auth dùng được với contract thật; token không persist hoặc sang S3; metadata persistence không trở thành demo fallback.

### T07 — Capture → review → commit → wardrobe; F02/F08

**Files:** Sửa frontend `src/data/adapters/http/{index.ts,dto.ts,mappers.ts,metadata.ts}`, `src/data/contracts/types.ts`, `src/features/capture/{CapturePage.tsx,ReviewPage.tsx}`, `src/features/wardrobe/WardrobePage.tsx`, `src/components/garment/GarmentImage.tsx`; thêm `tests/unit/http-capture.test.ts`, `tests/e2e/{capture-regressions.spec.ts,real-capture.spec.ts}`; chỉ sửa demo adapter nếu cần tương thích contract mới.

**Interface/đầu ra:** Hoàn thiện methods 1–8. Thêm optional capability `retryCapture?(draftId: string): Promise<CaptureDraft>` vào GarbloDataSource cho real, gọi đúng job retry, không reupload. Candidate thêm optional `status: draft|rejected|committed`, `garmentId`; CaptureDraft thêm optional `result: candidates|no_items` và `commitState: uncommitted|partial|complete`; không dùng `committedItemIds` có một phần để che cả editor.

- [x] Viết/chạy test đỏ: F02 `saved→SPA Back→resume→edit màu` giữ name/category cả fresh/stale cache; F08 hai `form.requestSubmit()` cùng synchronous tick chỉ tạo một logical operation. Không reload giữa bước để che lỗi F02; test double commit không thay cho test double create.
- [x] Persist operation key trước upload; pending key/body đi cùng retry/reselect đúng file. Mất 201 lookup theo key; mất 202 lookup theo source; có nhiều assets đang xử lý vẫn resume đúng nguồn. Không suy candidates rỗng là chưa enqueue.
- [x] Autosave diff từng field, name→details.display_name tối đa 80, màu rỗng→null; selected→draft/rejected. Giữ material/tags/styles/uncertainty không sửa; category chỉ clear uncertainty khi người dùng xác nhận rõ. Alias unknown ở review→chưa phân loại, không tự coi `other` là đã được user xác nhận.
- [x] F02: canonical response cập nhật cache, editor rehydrate khi không dirty/pending; sequence/epoch chặn response cũ. Partial PATCH lỗi phải reconcile, giữ lỗi và chặn commit tới khi save đủ. Có cảnh báo pending khi reload, không hứa browser luôn flush được lúc đóng tab.
- [x] F08: ref lock ngay đầu submit và adapter in-flight guard; commit đợi queue, lưu selection ổn định để replay cùng candidate IDs. Committed read-only, còn draft vẫn review được; no_items/all-rejected có UI riêng, không success wardrobe.
- [x] Poll queued/running với backoff 1.5→3→5→10 giây, dừng terminal/401/404/unmount/logout; offline/network timeout chỉ là mất theo dõi, không đổi job thành failed. Retry explicit gọi cùng job, không reset attempts/model/deadline.
- [x] Load media qua Blob, renew expired signed URL tối đa một lần; credentials omit/no Bearer trên S3, revoke object URL và có retry UI. Wardrobe IDs server, normalize category rồi filter và hiển thị đúng nơi lưu dữ liệu real.
- [x] Chạy `npm run test -- tests/unit/http-capture.test.ts`; chạy regression demo/real ở T09. Kiểm red→green của F02/F08 bằng patch do mình tạo, không revert thay đổi người khác.

**Gate:** Review/commit chỉ báo thành công sau response thật; F02/F08 pass và logical create/commit không duplicate khi replay.

### T08 — Context → composition → feedback; bỏ tính năng real chưa có API

**Files:** Sửa frontend `src/data/adapters/http/{index.ts,dto.ts,mappers.ts}`, `src/data/contracts/types.ts`, `src/features/lookbook/{ContextPage.tsx,SessionPage.tsx,OutfitBoard.tsx}`, `src/{app/Router.tsx,app/Shell.tsx,lib/domain.ts}`, `src/features/wardrobe/WardrobePage.tsx`; thêm `tests/unit/http-recommendations.test.ts`; recommendation E2E gộp trong `tests/e2e/real-capture.spec.ts` để kiểm cùng server IDs xuyên luồng.

**Interface/đầu ra:** Hoàn thiện methods 9–14; methods 15–19 trả lỗi có mục đích theo bảng disposition. `Outfit` thêm optional `itemsSnapshot: WardrobeItem[]`, `revision`; `LookbookSession` thêm optional `reason`, `missingCategories`, `scoreNotice`, giữ demo tương thích. Backend context/request/feedback schema hiện có không đổi.

- [x] Viết/chạy test đỏ cho wrapper `{context}` + stable Idempotency-Key và dress+shoes; unit kiểm mapping snapshots/PUT; browser kiểm snapshot sau PATCH wardrobe và feedback qua reload/re-auth. Lost-response recommendation và repeat/flip cần bổ sung fault E2E ở T09.
- [x] Lưu raw context draft local theo user, gửi validated context version 1. Required event/style, weather/transports optional, không biến lựa chọn Khác thành free prompt hoặc bịa thời tiết.
- [x] Render composition từ per-outfit server snapshots, không thay bằng global live wardrobe; giữ 0–3 outfit thực, reason/missing_categories, không bịa đủ ba hoặc gọi đây là ảnh AI sinh. Không để wardrobe refetch lỗi che session snapshot vẫn hợp lệ.
- [x] Feedback dùng PUT latest signal, chờ response trước success/invalidate; quản lý keyboard focus khi chuyển card/summary. Không dùng approval hoặc tăng wear_count từ like.
- [x] Không mở UI edits/messages. Với outfit revision >1, khóa feedback kèm giải thích tới khi có revision-binding policy; không dùng signal cũ như bằng chứng người dùng thích composition đã sửa. Đây là giới hạn slice local, không bảo đảm multi-client edits ngoài FE.
- [x] Ẩn WearOutfitButton/Impact links trong real; guard route `/impact` trước component có hooks. Demo tools giữ demo-only; không trả empty giả cho wear API chưa có.
- [x] Chạy `npm run test -- tests/unit/http-recommendations.test.ts`; E2E context/feedback thật ở T09.

**Gate:** Item IDs thuộc user, context/snapshot/feedback persist DB; demo không regression và real không truy cập wear/demo stores để giả kết quả.

### T09 — Regression và runtime riêng, không gọi provider trả phí

**Files:** Thêm frontend `playwright.real.config.ts`, backend `local-wsl/{deploy/compose.test.yml,tests/test-lifecycle.sh}`; cập nhật các integration tests hiện có và `garblo-backend/docs/BAO_CAO_TRIEN_KHAI_LOCAL.md`. Nếu cần controlled-provider process test, chỉ thêm harness trong `tests/`, không thêm production fake-provider flag.

**Interface/đầu ra:** Playwright demo hiện tại vẫn tự bật server demo; config real dùng `GARBLO_E2E_BASE_URL` trỏ stack test riêng, không reuse nhầm server demo. Test profile không có real AI keys, ports/volumes/bucket tách runtime người dùng. `test-lifecycle.sh` gọi chính start/stop scripts bằng project/env/override/test-mode của T05 và kiểm resolved target trước khi chạy, không gọi mặc định runtime. Các harness controlled-provider phải được gắn nhãn mock inference.

- [x] Chạy đầy đủ FE typecheck/lint/unit/build, BE offline/Ruff, scripts/static config checks theo mục 8. Npm lockfile là chuẩn, không chuyển package manager.
- [x] Trên DB tên kết thúc `_test`, chạy migration/PG lock/concurrent commit/upload-idempotency/lease recovery tests; storage test tạo bucket UUID riêng, kiểm private read, signed GET/expiry/CORS thật. Không dùng SQLite `create_all` thay bằng chứng migration.
- [x] Chạy real HTTP auth/upload/media/no-key smoke với `GARBLO_TEST_API_URL`; kiểm provider thiếu config trả lỗi trước enqueue. Theo dõi worker riêng/heartbeat; không coi test JobWorker trong process pytest là bằng chứng worker container đã làm việc.
- [x] Controlled harness có process worker claim→publish→commit, failure/retry cap và browser flow; external inference **MOCKED**. Restart/persistence kiểm riêng bằng lifecycle; fencing có unit/PG tests, chưa chứng minh kill giữa call/publish.
- [x] Lifecycle Docker thật từ cwd khác có khoảng trắng: start×2 → stop×2 → start → concurrent start×2; ID/count/checksum DB/ảnh giữ nguyên. Giữ volumes test sau chạy.
- [ ] Fault injection Docker: kill worker giữa call/publish, migration/dependency outage/readiness timeout, foreign-port conflict và cả hai repo dưới physical path có khoảng trắng. CLI-double/unit không thay thế runtime fault matrix.
- [x] Demo **64 passed**, real **8 passed** riêng (mobile/desktop); F02/F08, no_items/failed/retry cap, re-auth/hai user/late response, revoke consent, CORS/media thật và snapshots/feedback. Không đổi real thành demo; inference vẫn mocked.
- [ ] Browser fault matrix còn thiếu: lost 201/202 + refresh/reselect, partial PATCH/commit, URL expiry/network failure, JWT expiry, recommendation lost-response/repeat/flip. Unit/PG/S3 tests chỉ chứng minh từng lớp đã ghi trong report.
- [x] Scan định dạng key trong changed tracked/untracked files, rà `.dockerignore`/public env, canary host/container FE bundle và unit no-Bearer sang storage. Không phải audit mọi secret/log lịch sử; xem ghi nhận traceback fixture trong report.
- [x] Ghi commands/cwd/exit code/test counts và nguồn mock/real vào report ngay sau từng nhóm; không coi skipped/deselected là passed.

**Gate:** Runtime và inference-mocked E2E có bằng chứng riêng; không có lỗi chặn auth/ownership/idempotency/persistence; chưa được gắn nhãn AI live.

### T10 — Live có phép, runbook, report và bàn giao

**Files:** Hoàn thiện `garblo-backend/docs/{KE_HOACH_LOCAL_WSL.md,HUONG_DAN_CHAY_LOCAL_WSL.md,BAO_CAO_TRIEN_KHAI_LOCAL.md}`, cập nhật `garblo-frontend/docs/backend-integration.md` và các README/AGENTS liên quan theo diff nhỏ, giữ hai dòng README FE có trước. Thêm backend `local-wsl/tests/test-live.sh` làm entrypoint kiểm chứng có opt-in, không chạy trong normal startup.

**Interface/đầu ra:** Live test chỉ chạy khi `GARBLO_LIVE_TEST=1` và operator cung cấp `GARBLO_LIVE_IMAGE_PATH`, `GARBLO_LIVE_MAX_REQUESTS`, `GARBLO_LIVE_MAX_COST_USD` với budget đã duyệt. Script kiểm điều kiện trước inference, không tự điền budget hoặc thử model hàng loạt; giới hạn USD là guard dựa trên giá/usage đã xác minh, không tuyên bố có hard billing cap upstream nếu không có cơ chế đó.

- [x] Live **NOT TESTED**: chưa có key/model access/ảnh hợp lệ/quyền chi phí được operator cung cấp; entrypoint/điều kiện trong runbook. Không dùng fixture để đánh dấu live-pass.
- [ ] Nếu đủ điều kiện, gọi ít request nhất: ảnh được cấp phép → job OpenAI → candidates → user review/edit → commit → wardrobe; lưu model thực, request/job IDs đã redacted và counters. Không gọi generated image/lookbook.
- [ ] Để chứng minh recommendation với dữ liệu đã đi qua live, dùng wardrobe đủ role đã có hoặc một ảnh outfit được cấp phép; nếu chưa đủ đồ, đánh dấu gate recommendation live chưa đạt thay vì seed đồ demo vào tủ thật.
- [ ] FPT thiếu key vẫn nghiệm thu OpenAI-only; fallback policy được chứng minh bằng controlled tests. Chỉ thêm FPT wire-live nếu operator duyệt riêng; không cố gây lỗi OpenAI thật để tốn phí test fallback.
- [ ] Sau live, refresh browser và stop/start rồi so IDs/metadata/ảnh, context/feedback; kiểm log/bundle không lộ key. Không tự xóa user/garment/volume để dọn nghiệm thu.
- [x] Runbook có prerequisites, tài nguyên đo/ước lượng được ghi nhãn, ba bước env/key/start, URLs, log API/worker, stop, Windows localhost forwarding và cách kiểm tra khi không hoạt động, Docker/cổng/migration/CORS/key/CRLF/quyền thực thi; reset chỉ tài nguyên test riêng, yêu cầu xác nhận trước xóa.
- [x] Report có files thay đổi, lý do kiến trúc/layout, command/exit/count, actual endpoint/model đã gọi, mốc E2E, evidence real vs mock và trạng thái từng DoD. Đối chiếu Git status cả hai repo, giữ thay đổi có trước; không stage/commit/push.

**Gate:** Ba tài liệu tiếng Việt đầy đủ và checklist mục 10 phản ánh đúng bằng chứng. Nếu live chưa chạy, bàn giao phần đã kiểm chứng nhưng không gọi toàn bộ hệ thống DONE/live-ready.

## 8. Bộ lệnh kiểm tra khi thực thi

Các lệnh tái kiểm bên dưới chạy trong cwd ghi rõ; kết quả đã chạy trong report. Không chạy test integration trên DB/bucket người dùng. Venv/test image phải được chuẩn bị ở T00; không mặc định system `python3` đã có dependencies.

### Frontend — cwd `garblo-frontend/`

```bash
npm run typecheck
npm run lint
npm run test
npm run build
npm run test:e2e
npx playwright test --config playwright.real.config.ts
```

E2E demo/real có cấu hình khác nhau. Thiếu Chromium/Linux libraries thì ghi blocker, không nói browser pass; không tự cài OS packages khi chưa được phép.

### Backend — cwd `garblo-backend/`, trong venv hoặc test image

```bash
python -m pytest -q -m 'not integration'
ruff check src tests migrations
ruff format --check src tests migrations
python -m pytest -q -m integration
bash -n local-wsl/start-all.sh local-wsl/stop-all.sh
bash local-wsl/tests/test-scripts.sh
```

Integration cần `GARBLO_TEST_DATABASE_URL` với DB `_test`, `GARBLO_TEST_S3_ENDPOINT_URL`, `GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY`, `GARBLO_TEST_API_URL` trỏ stack test không có AI keys. Chỉ unset/set trong test process; không ghi đè `.env` runtime người dùng để chạy smoke no-key.

### Compose — cwd `garblo-backend/local-wsl/`

```bash
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml config --quiet
./start-all.sh
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml ps
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml logs --tail=100 api worker
./stop-all.sh
```

Không dùng plain `docker compose config` để xuất cấu hình đã nội suy secret. `docker compose ps`/HTTP 200 của FE không đủ chứng minh toàn stack ready. Log chỉ đọc/chia sẻ sau redaction; script phải in lệnh tương ứng absolute paths khi được chạy từ cwd khác.

## 9. Rủi ro và cách xử lý

| Rủi ro | Cách giữ mục tiêu |
|---|---|
| Key không có quyền model mặc định; thấy model trong list nhưng không chạy vision | T04 chốt profile/capability, T10 smoke ít call có phép; fail rõ, không giả key-only gate đã đạt |
| Docker hoạt động nhưng RAM/ports dùng bởi stack khác | Đo lại, dùng ports riêng, ghi tài nguyên thực; không kill/reconfigure stack khác |
| S3 ghi được nhưng DB chưa commit hoặc worker crash | Durable operation/lease/fencing; replay cùng key và đối soát; không hứa exactly-once object write/billing |
| PATCH từng candidate không atomic cả array | Diff/queue/canonical cache/reconcile, chặn commit khi chưa saved đủ; không ghi đè metadata từ cache cũ |
| Không có candidate revision/CAS, nhiều tab cùng sửa | Không tuyên bố chống mọi multi-writer; slice ưu tiên một editor, refetch/reconcile trước save/commit; nếu cần multi-client conflict mạnh thì lập thay đổi CAS riêng |
| Thu hồi consent sau checkpoint cuối | Công khai checkpoint semantics; không hứa cancel upstream/no-publish tuyến tính hoặc xóa URL đã ký; thêm shared locking chỉ nếu có yêu cầu riêng |
| Feedback hiện không gắn revision outfit | Không mở edit/messages ở FE; revision >1 khóa feedback, không suy learning đúng trên shared clients ngoài slice |
| URL public đúng host nhưng Blob fetch bị CORS/expiry | Bucket CORS thật, owner check, bounded renewal và test browser; không public bucket để né lỗi |
| Bootstrap test muốn xóa volume “cho sạch” | Project/volumes test riêng; không chạy down -v trên runtime; mọi reset có xác nhận và target cụ thể |
| Skill khuyến nghị khác spec hoặc tham chiếu skill chưa có | Spec/AGENTS ưu tiên, dùng skill hiện có, không tự cài framework/delegate hoặc mở rộng scope |

## 10. Definition of Done và quy tắc đánh dấu

Chỉ tick khi có command/test/evidence tương ứng trong report, không tick vì task đã viết code.

- [ ] G01: Copy sample + chỉ nhập OpenAI key đủ để khởi chạy; FPT key trống vẫn OpenAI-only và cảnh báo rõ.
- [x] G01/G06: Start/stop từ cwd khác, lặp hai lần và start đồng thời an toàn trong test project; không trùng service, serialize migrations mỗi lượt, không mất volumes/ảnh.
- [ ] G02: Lost upload/catalogue response, retry và refresh giữ đúng IDs; không dùng newest job heuristic.
- [x] G03: Auth/consent/ownership/401 có unit tests; browser hai user/late response/logout; FE bundle không chứa server canary/dev secrets đã kiểm. Không suy thành audit toàn bộ log; ghi nhận fixture traceback trong report.
- [x] G04: FE real gọi BE thật, không fallback demo; no_items/failure/retry E2E, partial PATCH/commit có xử lý/reconcile. Fault browser cho partial còn ở T09, chưa nghiệm thu toàn matrix.
- [x] G05: OpenAI/FPT policy, snapshot, audit, call/token/attempt budget có regression tests; không fallback lỗi bị cấm.
- [x] G06: F02/F08 có red→green regression, refresh/restart giữ metadata/ảnh; commit không tạo garment trùng.
- [x] G07: Recommendation composition/feedback persist DB; không formal approval/generated lookbook/wear real giả.
- [x] G08: PostgreSQL migrations, private storage, API và worker process được kiểm bằng runtime riêng, không chỉ unit mocks; external inference mocked.
- [ ] G05/G08: OpenAI image analysis live và chuỗi upload→review→commit→wardrobe có phép đã kiểm; nếu thiếu quyền/key thì mục này `NOT TESTED/BLOCKED`, không toàn bộ DONE.
- [x] G08: Ba tài liệu tiếng Việt đủ nội dung; report ghi file/command/cwd/exit/count, mốc E2E và endpoint/model thực đã gọi, tách real với mock.

**Tự review trước bàn giao kế hoạch:** Đối chiếu spec mục 1–12 với T00–T10; 19 methods đã có disposition; năm failure modes đều có task/test sở hữu; tên DTO/route mới được đánh dấu đề xuất; layout/versioning và giới hạn consent/model/multi-client được ghi rõ. Những điều này là coverage của kế hoạch, không phải chứng nhận sản phẩm.

**Bước tiếp theo:** Hoàn thiện fault matrix còn mở ở T06/T09; operator chuẩn bị key/quyền model/ảnh/ngân sách rồi chủ động nghiệm thu T10 theo runbook. G01 normal bootstrap và AI live vẫn **NOT TESTED**, G02 browser lost-response + refresh chưa đủ bằng chứng; không gọi toàn hệ thống DONE.
