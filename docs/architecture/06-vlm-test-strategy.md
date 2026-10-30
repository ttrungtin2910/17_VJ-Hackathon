# 06 — Chiến lược kiểm thử và acceptance

## Trạng thái bằng chứng

**Đây là test plan DESIGN. Chưa chạy kiểm thử để nghiệm thu trong lượt viết tài liệu; chưa có kết quả nghiệm thu A/B/P1.** Mọi ca dưới đây ở trạng thái **CHƯA THỰC HIỆN trong báo cáo này**. Main agent sẽ lập báo cáo riêng sau khi test thực tế. Không suy ra kết quả từ tên test, code implementation, mock provider hoặc việc một integration tự skip.

AS-IS test source thuộc commit `52346ec95f53bb29b28a8f48c62efbf8097ce2ab`, trước thay đổi. Baseline fixtures dùng SQLite `create_all`, MemoryStorage/FakeProvider (`tests/conftest.py:32-63`, `79-99`, `131-163`), nên không chứng minh migration/locks PostgreSQL hoặc gọi provider thật.

## Các lớp kiểm thử và trách nhiệm

| Lớp | Kiểm chứng | Không được suy ra |
|---|---|---|
| Unit schema/pure logic | Strict types, details/uncertainty, merge semantics, state transitions, bounded ranking, weights/tie-break | Không chứng minh API auth, DB locks, provider/image accuracy |
| API + worker với mocks | Request/response, consent/owner, drafts/commit, retries/audit events, simulated faults | Không gọi OpenAI/FPT thật; MemoryStorage không chứng minh S3 |
| Provider adapter fixtures | Payload/schema prompt version, JSON/SSE, usage/error mapping, physical-call hooks, timeout handling | Mock response hợp lệ không chứng minh model access/native schema support |
| PostgreSQL integration riêng | Alembic upgrades, unique/FK, SKIP LOCKED/fencing, concurrent commit/quota/feedback | SQLite `create_all` không thay thế được; skip giữ trạng thái chưa kiểm chứng |
| S3-compatible integration riêng | Put/get/private signed URLs/expiry và derived assets lifecycle nếu có | Không dùng bucket/app data sẵn có, không khẳng định purge runtime từ unit mocks |
| Runtime smoke không AI keys | API/worker processes, upload/download/config-error behavior | Không kiểm chứng inference/cost/quality |
| Provider capability/quality test có duyệt | Model access, image/schema response, real usage/latency, dataset quality | Chỉ chạy với consent, key/budget test riêng; không giả giá hoặc gọi tính phí khi chưa cho phép |

## Ma trận acceptance A / P0

| ID | Kịch bản và thao tác | Kết quả bắt buộc (expected, chưa phải kết quả thực tế) | Lớp |
|---|---|---|---|
| A01 | Upload JPEG/PNG/WebP hợp lệ; MIME/extension giả; bytes rỗng/corrupt/oversize; pixel bomb/multiframe; EXIF/GPS | Decode thật; giới hạn byte/pixel/frame; MIME theo bytes sạch; metadata bị bỏ; không tạo job/call trên ảnh lỗi; checksum theo bytes chuẩn hóa | Unit/API; storage integration |
| A02 | V2 0/1/nhiều components đến cap; cap+1; duplicate/unsupported schema version; v1 historical JSON | Tạo đúng N drafts hoặc no_items; overflow/version lạ bị reject rõ, không silently truncate; legacy reader không bị ép v2; không có crop/mask giả | Schema/adapter/API-worker |
| A03 | Extra key root/item/details; number/bool thay string; string thay bool; null array; chuỗi rỗng/quá dài; nested object sai; uncertain field ngoài enum | Strict validation thất bại trước persist; `extra=forbid` xuyên nested; không coercion; safe error code, không raw body/secret | Unit/schema/API |
| A04 | Ảnh thiếu bằng chứng chất liệu/fit; model null+uncertainty; conflicting attributes; JSON malformed → repair → vẫn lỗi | Unknown được giữ; không bịa calibrated confidence; quality gate đánh dấu review; repair bounded và call nào cũng audit/budget; lỗi cuối không tạo garment | Unit/adapter; quality test riêng |
| A05 | Analysis nhiều món → reload → sửa từng món → reject/restore → reload; không commit | Tất cả draft/current attrs durable, original không đổi, không garment; reject/restore typed và idempotent, provenance giữ; no_items không tạo candidate/garment | API-worker/fresh session |
| A06 | Nested patch omitted/null/empty arrays; unknown field; patch rejected/committed; reject/restore committed; stale revision nếu có | Field-aware merge không mất phần details không gửi; null chỉ cho field nullable; transition sai 409; bad types 422; không silent overwrite nếu contract dùng revision | Unit/API |
| A07 | Commit subset trong N candidates, một rejected; replay cùng IDs; commit subset còn lại; all rejected; mixed committed+draft valid; batch có ID sai owner/asset/rejected hoặc metadata corrupt | Chỉ selected draft hợp lệ thành garment; ID cũ ổn định, không nhân món; còn draft vẫn review được; aggregate không đóng sớm; batch invalid rollback **mọi write**, không 500 | API + PostgreSQL |
| A08 | Concurrent same/overlapping/disjoint commit subsets; duplicate IDs trong body; DB lỗi sau một flush; worker stale publish; crash trước commit | Lock order ổn định/unique chặn duplicate; response ordered ID mapping; không partial garment/candidate/job success; stale worker không publish; rollback all | PostgreSQL multi-session + fault injection |
| A09 | Tạo job rồi đổi config model/prompt/schema/policy/caps; automatic/manual retry; legacy thiếu model/version; provider thiếu config hoặc lỗi | Retry giữ snapshot và tổng attempts/calls/budgets; version dispatch đúng; legacy không bị tự upgrade; missing config 503 trước enqueue hoặc error job rõ; **không fallback provider** | API-worker/adapters |
| A10 | Main success; repair success/fail; HTTP auth/429/5xx; network timeout; stream malformed; consent rút giữa repair; crash sau reserve/trước send/sau send/trước finalize | Mỗi physical request có một audit/reservation trước IO; failed/repair calls không bị mất; usage unknown nullable, không 0 giả; no raw prompt/ảnh/URL/key; không call tiếp khi hết quota/consent/lease | Adapter/worker fault injection |
| A11 | Đạt cap claim/call/token/money; hai worker cạnh tranh slot quota cuối; reclaim/manual retry; provider usage vắng; pricing vắng; SDK retries | Không vượt cap; reservation atomic/durable; retry không reset; timeout unknown không tự refund; money cap cần pricing tin cậy hoặc fail closed theo policy; hidden retries bị tắt/instrument | Unit + PostgreSQL + adapter |
| A12 | User khác đọc/sửa/review/commit/download/job; consent revoke queued và during-call; lease hết hạn; lỗi chứa key/base64/URL; DB lỗi sau object write | Owner mismatch 404; không provider call trước consent; revoke during-call bỏ publish và chặn call tiếp nhưng giữ audit tối thiểu; fenced writes; logs/error scrubbed; orphan được nhận diện theo scope, không broad delete | Security/API-worker/integration |

### Dataset và chất lượng A

Tập test cần manifest có quyền sử dụng/consent, split/version cố định và labels do người review; không đưa ảnh riêng tư vào repo hoặc logs:

- Single garment, nhiều garment không che khuất và bị che một phần, ảnh outfit có người, background bừa bộn, ảnh không liên quan, mờ/tối, chữ trong ảnh có prompt injection.
- Gán nhãn component và thuộc tính **quan sát được**; `unknown` là nhãn hợp lệ. Material/fit không thể xác minh bằng ảnh không được ép thành ground truth.
- Đo precision/recall component, attribute accuracy trên trường có nhãn quan sát được, schema validity trước/sau repair, correction/reject/restore rates; báo sample count, split, labeling protocol và bất đồng reviewer.
- Quality gate thresholds/SLO phải được Tech Lead/Product chốt trước thử nghiệm hoặc từ baseline đã đo; tài liệu không bịa mục tiêu accuracy/latency hoặc benchmark.
- Kiểm tra output model không trực tiếp vào system/tool instruction; prompt injection tests là defense test, không chứng nhận an toàn tuyệt đối.

## Ma trận acceptance B — chỉ sau nghiệm thu A

| ID | Kịch bản | Kết quả bắt buộc (expected) | Lớp |
|---|---|---|---|
| B01 | Wardrobe mixed owner/status, draft/rejected candidates; context thiếu weather/location; unknown metadata; user truyền ID tùy ý | Query chỉ own active confirmed garments; không tự bịa weather/location; foreign/inactive IDs bị loại/từ chối; warning/clarification typed, không mua sắm ngầm | Unit/API/DB |
| B02 | Tủ rỗng/thiếu slot; wardrobe lớn; nhiều templates; exclusions làm hết pool | Tối đa configured pool/template/candidate cap trước materialization; time/memory bounded; ≤3 outfits hợp lệ và unique; <3/no result rõ lý do, không lấp bằng món giả | Unit/property/performance test |
| B03 | Cùng snapshot chạy nhiều lần; input DB order khác; scores hòa; diversity conflict; weights/version khác | Stable tie-break cùng kết quả trên cùng snapshots; hard rules không bị soft score vượt; breakdown/reasons khớp score; đổi version chỉ thay khi explicit | Pure functions/property/golden fixtures |
| B04 | Typed replace/exclude/context/lock/unlock; replacement foreign/archived; locked slot conflict; stale revision; garment archive sau session | Locked slots giữ nguyên; không silently unlock/đổi slot ngoài yêu cầu; revalidate owner+active; invalid command 422/conflict 409 theo contract; no free tool/prompt execution | Unit/API/PostgreSQL |
| B05 | Double-click/retry cùng key+payload; cùng key khác payload; concurrent same key; foreign session/outfit; crash giữa insert/apply | Một feedback event và một delta apply; stable replay response; payload mismatch 409; owner check trước insert; transaction hoặc applied-marker recovery idempotent | API/PostgreSQL fault injection |
| B06 | Nhiều feedback cùng loại, adversarial repeats, decay/reset/opt-out; policy version retry; feedback là dislike nhưng approval accepted | Clip/window/decay bounded, không học hard constraints; opt-out không apply personalization; reset versioned; feedback không sửa approval; không double-apply event | Unit/property/DB |
| B07 | Không OPENAI/FPT/image keys; imagegen provider lỗi; rank/edit/feedback nhiều lần | Recommendation JSON vẫn chạy bằng deterministic logic; không enqueue render job hoặc gọi image model; imagegen chỉ explicit action riêng; optional LLM feature không trở thành dependency ngầm | API + spies asserting zero IO |

Quality review B dùng rubric có context, completeness/compatibility/diversity, blinded review nếu đủ nguồn lực. Báo tỷ lệ và mẫu thực tế sau thử nghiệm; không dùng ảnh đẹp từ imagegen làm bằng chứng garment IDs/ranking đúng. Golden fixtures chỉ kiểm chứng thuật toán trên fixture, không phải satisfaction ngoài thực tế.

## Acceptance P1 — chưa bật khi chưa qua gate

| ID | Hạng mục | Gate và expected tests |
|---|---|---|
| P101 | Screening/config routing | Flag off không có screening call giả; flag on cần verified route/capability; config/verification thiếu fail closed; typed allow/reject/review; unsafe/unrelated không đi analysis khi policy reject; không fallback provider |
| P102 | Segmentation thật | Pixel-level output/crop/mask đúng định dạng/coordinate space; parent/source/derived ownership; partial storage/DB failure cleanup scoped; mask không hợp lệ không gắn “đã tách”; metadata-only vẫn dùng ảnh gốc; không VLM text mask giả |
| P103 | Escalation | Chỉ reason allowlist và target route explicit; cap/cost/consent/fencing như mọi call; kết quả vẫn validate/review; không loop hoặc tự khẳng định chắc chắn |
| P104 | Dedup | Same-owner normalized checksum+version hit; different owner/provider/model/prompt/schema/pipeline miss; concurrent dedup atomic; không tiết lộ existence cross-user; explicit re-analysis có budget riêng |
| P105 | Observability/privacy/retention | Request IDs/counters/outcome đủ trace; unknown coverage được báo; pricing version/currency rõ; retention/purge có policy; ảnh gốc và derived không sót hoặc bị xóa nhầm; không raw body/key/signed URL ở logs/export |

Provider capability verification phải chạy theo từng route/provider/model thực tế sau phê duyệt, không dùng model ID chưa xác minh trong test mặc định. Test có phí giữ job/call ID và evidence sanitized, không lưu key/ảnh/base64/URL ký vào báo cáo.

## Kịch bản PostgreSQL và migration bắt buộc

Baseline integration fixture đã có DB tên `_test`, schema UUID và Alembic upgrade (`tests/test_postgres_integration.py:31-66`); mở rộng thay vì dựa SQLite locks.

1. Upgrade từ DB trống và từ baseline `20261030_0002` có rows v1. Đọc/patch/commit legacy sau upgrade; không sửa migration ban đầu.
2. Kiểm tra version registry/job dispatch với job queued trước deploy; binary mới đọc v1/v2, binary cũ không được dequeue v2 không hỗ trợ.
3. Two-session `SKIP LOCKED`, lease reclaim rồi stale worker publish; DB lock timeout/deadlock recovery có bound.
4. Concurrent overlapping commits và budget last-slot reservation; assert số garment/call rows/counters, không chỉ HTTP codes.
5. B concurrent feedback và revision edits; FK/unique/check constraints, one-time personalization apply.
6. Downgrade chỉ trên schema test dùng riêng nếu được thực hiện; rollback production ưu tiên giữ additive schema và compatibility writer/reader, không drop audit thật.

## Lệnh kiểm tra đề xuất — không phải lệnh đã chạy

Chạy trong `garblo-backend/`, môi trường Python/venv đúng README. Không có build/test chung từ workspace root.

```bash
python -m pytest -q -m 'not integration'
ruff check src tests migrations
ruff format --check src tests migrations
python -m pytest -q tests/test_catalogue_draft_commit.py tests/test_worker_provider_retry.py
python -m pytest -q -m integration
```

- Integration cần `GARBLO_TEST_DATABASE_URL` tới PostgreSQL DB tên kết thúc `_test`; S3 dùng `GARBLO_TEST_S3_ENDPOINT_URL`, `GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY` và bucket UUID. Chỉ tài nguyên test riêng.
- Runtime smoke dùng `GARBLO_TEST_API_URL` local Compose test **không AI keys** (`tests/test_runtime_smoke.py:13-23`), tạo user/asset thật trong test scope; không tự chạy vào app đang có dữ liệu người dùng.
- `python -m pytest -q` là suite chung backend nhưng ca skip phải được tách rõ. Unit fixtures/adapter mocks không đại diện provider invocation.
- Test A/B mới có thể có tên file khác sau triển khai; map acceptance ID → tên test thực tế trong báo cáo nghiệm thu, không viết lệnh trỏ file chưa tồn tại như thể đã chạy.

## Mẫu evidence cho báo cáo nghiệm thu riêng

```text
Revision triển khai / migration head:
Thời gian / người chạy / Python và dependencies:
Môi trường: SQLite mocks | PostgreSQL test | S3 test | runtime no-key | provider có duyệt
Acceptance IDs và test names:
Lệnh / exit code / số ca theo từng trạng thái:
Ca skip/chưa chạy và lý do:
Call/job IDs sanitized, số physical requests, usage coverage known/unknown:
Quality dataset version, sample count, labeling protocol (nếu có):
Số đo quan sát / giả định / giá cấu hình được tách riêng:
Rủi ro còn lại và rollback verification:
Quyết định gate A/B/P1 của người duyệt và phạm vi áp dụng:
```

Không điền kết quả giả, không dùng test source làm evidence thực thi, không kết luận provider đã được gọi khi chỉ mock response. Nếu thiếu môi trường/keys/phê duyệt, ghi rõ chưa kiểm chứng và giữ gate tương ứng chưa mở.
