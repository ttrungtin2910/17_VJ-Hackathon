# GARBLO — BACKEND IMPLEMENTATION HAND-OFF FOR CODING AGENT

**Loại tài liệu:** Prompt nhiệm vụ + execution plan + acceptance tests, giao trực tiếp cho AI coding agent đang có quyền đọc/sửa **repository backend**.  
**Ngày:** 30/10/2026  
**Ưu tiên:** P0 vận hành được MVP; ưu tiên AI provider và luồng 3 must-have.  
**Ngôn ngữ:** Trao đổi, báo cáo, comment nghiệp vụ bằng tiếng Việt; giữ tên file/API/biến bằng tiếng Anh.  
**Trạng thái:** YÊU CẦU TRIỂN KHAI. Đây **không phải** tuyên bố code đã được chỉnh sửa hoặc kiểm thử thành công.

> **AGENT — BẮT ĐẦU Ở ĐÂY:** Hãy đọc toàn bộ hand-off này, điều tra mã nguồn thật trước khi sửa, triển khai các yêu cầu **P0 Backend** theo thứ tự, chạy test và đưa bằng chứng `file:line`/lệnh/test. **Không chỉ phân tích hay viết kế hoạch; phải thực sự sửa source code** trong repo được giao. Không bịa kết quả kiểm thử khi thiếu service, key hoặc network. Nếu một P0 bị chặn, ghi blocker và tiếp tục các P0 độc lập. Đừng hỏi để xác nhận lại các quyết định đã chốt ở đây.

---

## 0. MISSION & NGUYÊN TẮC BẤT BIẾN

**Mission:** Đưa backend Garblo từ bộ API/worker đang có sang một bản MVP dùng được với OpenAI API trực tiếp (mặc định), đồng thời bổ sung FPT AI Marketplace VLM `Qwen2.5-VL-7B-Instruct` cho **phân tích ảnh quần áo**. Khi code tích hợp hoàn tất, operator **chỉ cần thêm một biến `FPT_VLM_API_KEY` vào `.env` và restart API + worker** để có thể **chọn** FPT ở request catalogue; không cần sửa code, bổ sung URL/model hay cài thêm package thủ công. Nếu request **không chọn provider**, hệ thống **luôn dùng OpenAI** kể cả đã có FPT key.

### Quy tắc khóa

1. **DEFAULT = `openai`** cho mọi yêu cầu không chỉ định `vlm_provider`. Tuyệt đối không suy ra mặc định từ việc có FPT key.
2. **EXPLICIT `fpt`** chỉ dành cho tác vụ vision / image-to-component (`catalogue`). Không sử dụng Qwen VLM để **sinh ảnh**, VTON hay sửa ảnh.
3. **Image generation = OpenAI Images API** (nếu deployment/account được cấp quyền); không làm hỏng luồng lookbook hiện có.
4. **Không silent fallback qua provider khác.** Không chuyển ảnh riêng tư sang nhà cung cấp khác sau lỗi mà không có lựa chọn phù hợp.
5. **Giữ backward compatibility** với API `/v1` hiện có, JWT, owner checks, consent, PostgreSQL `ai_jobs`, storage S3-compatible, worker, migrations, approvals.
6. **Không rewrite backend từ đầu, không tách microservices, không tự thêm FPT Agent EOS, GPU, Redis/Celery, LangGraph** chỉ vì cần adapter FPT.
7. **Không sửa frontend** trong nhiệm vụ này. Thay vào đó bàn giao hợp đồng API và các curl để FE agent tích hợp.
8. **Không làm lộ secret:** không commit `.env` hoặc token/ảnh Base64, không log prompt ảnh nhạy cảm, không đưa API key vào response.
9. **Tôn trọng working tree:** kiểm tra `git status` đầu tiên, giữ nguyên thay đổi sẵn có của người khác, không `git reset --hard`, `git clean -fd`, `rm -rf` tùy tiện; không commit/push nếu chưa được yêu cầu.
10. **Triển khai có bằng chứng:** phân biệt `IMPLEMENTED`, `TESTED_WITH_MOCK`, `TESTED_WITH_REAL_PROVIDER` và `BLOCKED`.

## 1. CĂN CỨ (không giả vờ đã mở repo)

**Scope 1 — cuộc họp kỹ thuật:** Ba must-have trước 03/11, release đầu dự kiến 05/11:

- `imageAnalystWS`: ảnh → nhận diện/bóc tách component → review/edit → tủ đồ; **draft phải lưu bền** để không tốn token khi người dùng gián đoạn.
- `wardrobeManagerWS`: lấy/lưu item thật, metadata, lọc và phân loại.
- `lookbookStylistWS`: tủ đồ → phối lookbook theo **preset prompt**, tạm khóa prompt tự do; feedback thích/không thích. Voice và chỉnh look chi tiết không phải đường tới P0.

**Báo cáo Backend AS-IS ngày 30/10 (chưa kiểm chứng lại trực tiếp trong repo):**

- FastAPI (`src/garblo/main.py`, `api/routes.py`), Pydantic Settings (`config.py`), SQLAlchemy/PostgreSQL (`models.py`, `database.py`), Alembic migrations, S3-compatible RustFS (`storage.py`).
- API và background worker (`worker.py`, `garblo-worker`) tách tiến trình, dùng PostgreSQL `ai_jobs` + `FOR UPDATE SKIP LOCKED`.
- AI code tại `ai.py` dùng `AzureChatOpenAI` và Azure OpenAI SDK; BTC lại cấp **OpenAI organization credit**, không phải Azure credit.
- Các endpoint cốt lõi: `POST /v1/assets`, `POST /v1/assets/{asset_id}/catalogize`, `GET /v1/jobs/{job_id}`, `POST /v1/jobs/{job_id}/retry`, `GET/PATCH /v1/garments`, `POST/GET /v1/lookbooks`, `GET /v1/lookbooks/{id}/looks`, `POST /v1/approvals/{id}/decision`.
- Có 11 bảng và một migration đầu `20261018_0001_initial_schema.py`, nhưng **chưa có bằng chứng E2E thật**; test cũ từng dừng do thiếu dependency. `lookbook` image generation hiện tạo từ **metadata**, không truyền ảnh garment gốc; variation chưa chỉnh ảnh đầu ra làm ảnh tham chiếu.

**Technical Guideline BTC:** AWS $120/đội, OpenAI $1.000/organization, FPT Token Factory $50/đội, FPT AI Agent EOS $200/account; tài nguyên dùng 25/10–11/11/2026; AWS chỉ hai region `ap-northeast-1`, `us-east-1`. Chưa xác minh quyền tạo EC2/S3/RDS; **không giả định đã deploy**.

**FPT VLM — nguồn bên ngoài:**

- Model: <https://marketplace.fptcloud.com/en/models/qwen2.5-vl-7b-instruct>
- API: <https://ai-docs.fptcloud.com/api-reference/ai-marketplace/api-reference/api-integration-vision-language-model-md>
- Endpoint user cung cấp: **`POST https://mkp-api.fptcloud.com/v1/chat/completions`**.
- Tài liệu marketplace hiển thị response dạng `{"code":200,"data":{"choices":[...]}}`; cần hỗ trợ cả `choices` top-level nếu runtime trả kiểu OpenAI-compatible.
- Mẫu user đưa có `stream: true`; **cần xác minh thực tế `stream: false` có hoạt động với key của đội hay không**. Không suy đoán.

**Lưu ý về nguồn:** Những tên file, endpoint cũ, giới hạn 10 MiB và state ở đây đến từ *báo cáo*; agent phải đối chiếu mã nguồn thực và ghi khác biệt trước khi sửa. Những endpoint/schema mới bên dưới là **DESIGN**, không phải chức năng đã có.

---

## 2. BƯỚC ĐẦU TIÊN: BASELINE SOURCE AUDIT (BẮT BUỘC)

Trước mọi thay đổi, thực hiện:

```bash
pwd
git status --short
git rev-parse --short HEAD
find . -maxdepth 2 -type f | head -80
rg -n 'AzureChatOpenAI|AzureOpenAI|catalogize|ai_jobs|retry|provider|FPT|consent|approval|lookbook' src tests migrations .env.example pyproject.toml 2>/dev/null
```

Điều tra trực tiếp, trích `file:line` và ghi baseline:

1. Luồng HTTP catalogue (request body / schema hiện tại) → enqueue → worker → Azure analysis → tạo `garments`.
2. Job `input_ref`/`output_ref`, enum `status`, tiêu chí retry, timeout, lock và idempotency hiện có.
3. Settings `.env`, cách khởi tạo Azure client, model name; model ảnh của lookbook và cách trả lỗi thiếu config.
4. Storage bytes, MIME detection, giới hạn kích thước, signed URL, EXIF, cách tránh SSRF hoặc rò rỉ ảnh.
5. Consent: check tại HTTP và tại worker; xác thực JWT và kiểm tra owner.
6. Migration hiện tại và constraints/index; tests, fixtures và lệnh chạy thực tế.
7. Kiểm tra resource/SDK thực dùng trước khi thêm dependency. Không thay package version hàng loạt nếu không cần.

**Sau audit, lập bảng `AS-IS → Target → File sửa`** trong báo cáo cuối. Không cần tạo tài liệu phân tích dài trước khi bắt tay implement.

---

## 3. P0-A: MULTI-PROVIDER VLM, OPENAI MẶC ĐỊNH

### 3.1. Environment contract — phải giữ đúng

`.env.example` đề xuất (không đưa key thật vào source):

```dotenv
# PRIMARY — OpenAI direct API, không phải Azure OpenAI
OPENAI_API_KEY=
# Model cụ thể theo cấu hình/backend thực tế và quyền organization
OPENAI_VLM_MODEL=
OPENAI_IMAGE_MODEL=

# OPTIONAL — chỉ cần dòng này với key thật để cho phép chọn FPT VLM
FPT_VLM_API_KEY=

# OPTIONAL OVERRIDES; KHÔNG CẦN khai báo nếu dùng mặc định
# FPT_VLM_BASE_URL=https://mkp-api.fptcloud.com/v1
# FPT_VLM_MODEL=Qwen2.5-VL-7B-Instruct
# FPT_VLM_TIMEOUT_SECONDS=60
```

**Chú ý:** Giá trị `OPENAI_VLM_MODEL`/`OPENAI_IMAGE_MODEL` phải được agent chọn theo model đã có trong repo hoặc được team thực sự xác minh, không lấy placeholder trong `.env.example` rồi cho rằng cấu hình đủ. Cần document thêm cách config OpenAI image model.

- `FPT_VLM_API_KEY`: optional (`SecretStr | None` hoặc tương đương); empty/absent = `fpt` unavailable.
- Default `FPT_VLM_BASE_URL`: `https://mkp-api.fptcloud.com/v1` (đối với client SDK); nếu dùng raw HTTP và ghép path phải tạo **đúng một** `/v1/chat/completions`.
- Default `FPT_VLM_MODEL`: `Qwen2.5-VL-7B-Instruct` (case-sensitive).
- Default `FPT_VLM_TIMEOUT_SECONDS`: cấu hình an toàn, đề xuất 60s; timeout & retry tách khỏi timeout request API vì job chạy nền.
- `openai` là **default hard-coded của provider selector**, không phải biến tự động đổi khi key có mặt.
- Cả API và worker đọc cùng `.env`/runtime env; **restart** để nhận key mới, **không cần rebuild** Docker image. Với Compose, đảm bảo `env_file`/environment được truyền cho **cả hai container**.
- Không tạo thêm bắt buộc `FPT_VLM_ENABLED`, `FPT_VLM_ENDPOINT` hay `DEFAULT_VLM_PROVIDER=fpt`.

### 3.2. Bảng behavior bắt buộc

| Request | FPT key | Provider được dùng | Kết quả |
|---|---|---|---|
| Không gửi `vlm_provider` | Không có | `openai` | Chạy OpenAI nếu OpenAI configured |
| Không gửi `vlm_provider` | Có | `openai` | **Vẫn OpenAI**, FPT không nhận ảnh |
| `"vlm_provider":"openai"` | Bất kỳ | `openai` | Chạy OpenAI |
| `"vlm_provider":"fpt"` | Có | `fpt` | Gọi Qwen FPT |
| `"vlm_provider":"fpt"` | Không có | Không chạy | HTTP 503 `provider_not_configured` trước enqueue |
| `"vlm_provider":"x"` | Bất kỳ | Không chạy | HTTP 422 validation error |
| OpenAI timeout/429/5xx | Có | Vẫn OpenAI | Retry đúng **provider cũ**, không tự chuyển FPT |
| FPT timeout/429/5xx | Có | Vẫn FPT | Retry đúng **provider cũ**, không tự chuyển OpenAI |
| Job `lookbook` hoặc `variation` | Có | OpenAI image generation | **Không** gọi Qwen VLM để tạo ảnh |

**Phạm vi thay đổi provider:** P0 chỉ `catalogue` image analysis. Phần stylist reasoning, Critic hay VTON không nằm trong FPT switch của lần này.

### 3.3. API contract — backward compatible

Bổ sung **field optional** `vlm_provider: Literal["openai", "fpt"] = "openai"` trong schema đầu vào **hiện có** của:

```http
POST /v1/assets/{asset_id}/catalogize
Authorization: Bearer <garblo-access-token>
Content-Type: application/json

{"vlm_provider":"fpt"}
```

- Nếu endpoint hiện đã có body nhiều field, **mở rộng** schema cũ; không thay bằng request chỉ có field trên.
- Nếu endpoint hiện không có body, cho phép body không gửi / body `{}` mà vẫn chạy OpenAI; kiểm thử cả clients cũ.
- Validate sở hữu asset + consent AI + đủ config provider **trước khi enqueue**.
- Snapshot `provider`, `model`, `requested_at` trong `ai_jobs.input_ref` (nếu JSON đã phù hợp) hoặc migration mới tối thiểu; không sử dụng global mutable provider.
- `GET /v1/jobs/{id}` vẫn trả contract cũ; thêm metadata provider/model nếu an toàn và không breaking. Error code/domain không chứa raw upstream body/key/ảnh.
- `POST /v1/jobs/{id}/retry` giữ **provider snapshot** của job gốc.
- Không tạo API `GET /v1/ai/providers` trong P0 nếu không cần thiết; nếu FE cần capability endpoint thì ghi rõ P1.

### 3.4. Interface gợi ý (không ép path/names khi repo khác)

```python
from typing import Literal, Protocol

VLMProvider = Literal["openai", "fpt"]

class GarmentAnalyzer(Protocol):
    async def analyze(self, image: bytes, mime_type: str, prompt: str) -> dict: ...

# resolve(provider="openai") -> OpenAI direct image analyzer
# resolve(provider="fpt")    -> FPT Qwen image analyzer (key required)
# Không bao giờ: provider = "fpt" if settings.fpt_vlm_api_key else "openai"
```

Adapter được phép viết trong `src/garblo/ai_providers/` hoặc tổ chức tương đương nếu ít invasive hơn. Trong worker async/sync phải nhất quán với framework hiện hữu; không gọi blocking I/O trong async loop không kiểm soát.

### 3.5. FPT wire protocol & sample request

Sử dụng `POST https://mkp-api.fptcloud.com/v1/chat/completions`, headers:

```http
Content-Type: application/json
Authorization: Bearer <FPT_VLM_API_KEY>
```

Payload **minh họa** cho một ảnh JPEG từ private storage:

```json
{
  "model": "Qwen2.5-VL-7B-Instruct",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Analyze only visible clothing items in this picture. Return structured JSON. Do not invent materials or confidence."
        },
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/jpeg;base64,<BASE64_JPEG_BYTES>"
          }
        }
      ]
    }
  ],
  "temperature": 0,
  "max_tokens": 1024,
  "stream": false
}
```

Implement:

- Lấy bytes qua storage adapter, xác minh owner/consent và MIME thật; encode Base64 theo **MIME chính xác**, không gửi S3 object public hoặc remote URL tùy ý.
- Tránh gửi EXIF/GPS nếu có thể; nếu thực hiện xử lý/chuẩn hóa ảnh cần bảo toàn pixel và xử lý ảnh lỗi/ảnh cực lớn. Không làm ảnh test không thể dùng vì ép định dạng sai.
- Mẫu gốc của user có `top_k=40`, `top_p=1`, penalties=0, `stream=true`. Không nhất thiết truyền mọi tham số trong P0; ưu tiên cấu hình tối thiểu, deterministic extraction. Với key của đội, smoke test non-stream. **Nếu FPT thực tế chỉ trả SSE/stream thì tích hợp parser SSE hoàn chỉnh**, không coi body là JSON non-stream.
- Không log full data URI, base64, API key, raw image, upstream error có thể chứa dữ liệu nhạy cảm.

### 3.6. FPT response normalizer

Hai dạng cần test fixture:

```json
{"code":200,"message":"Chat completion successful","data":{"choices":[{"message":{"role":"assistant","content":"{\"items\":[]}"}}]}}
```

```json
{"choices":[{"message":{"role":"assistant","content":"{\"items\":[]}"}}]}
```

- Kiểm HTTP status và app-level `code` nếu có, lỗi rõ khi `code` khác success.
- `root = data` khi `data` chứa `choices`, ngược lại root response; lấy `choices[0].message.content` (non-stream); với SSE thì gom `delta.content` theo thứ tự và chỉ kết thúc khi stream hoàn tất.
- Model có thể trả string, text content parts, Markdown fenced JSON hoặc JSON không hợp lệ. Parse an toàn rồi validate **schema domain đang có**; cho phép một lần repair/re-ask có kiểm soát, không giả response đẹp để pass.
- Canonical fields phải tương thích pipeline hiện tại: category, color, material, season, tags; nếu multi-item candidates đã được hỗ trợ thì map từng item. Unknown = `null`/unknown theo schema, không bịa chất liệu/confidence.
- Output `not_clothing`/`no_items` phải tạo domain result hoặc lỗi nghiệp vụ rõ **không commit garment sai**.
- Provider/model/latency_ms/usage (nếu API trả) có thể lưu trong `job` metadata, không cần một bảng observability riêng.

### 3.7. Error matrix & retry

| Error | Hành vi tối thiểu |
|---|---|
| Missing key của provider được chọn | HTTP 503 trước enqueue, mã `provider_not_configured` |
| Invalid provider | HTTP 422 trước enqueue |
| Provider 401/403 | `provider_auth_error`, không retry tự động vô hạn |
| 429 / 5xx / connection timeout | `provider_transient_error`, bounded retries + exponential backoff có jitter, sau đó `failed` |
| JSON/schema không hợp lệ | `invalid_model_output`, không tạo garment giả |
| Không thấy quần áo | `not_clothing` / candidate rỗng, không báo success lưu tủ |
| Consent bị rút trước xử lý | Worker re-check và không gọi provider |
| API/worker restart | Job có trạng thái nhất quán, không đúp item/approval |
| Retry job | Giữ chính `provider` và `model` ở thời điểm tạo job |

Khi retry có liên quan các side-effect (tạo `garments`, asset state), kiểm soát **idempotency**. Không để `ai_jobs` `succeeded` trong khi DB write thất bại. Không thay đổi status enums cũ khi chưa xem code.

### 3.8. OpenAI direct adapter

- BTC cấp credit cho `platform.openai.com` organization; backend Azure adapter hiện tại **không** tự dùng được credit này.
- Tạo direct OpenAI client/adapter bằng `OPENAI_API_KEY`; dùng endpoint/SDK phù hợp Vision và Images API. Dùng model được quyền sử dụng thực tế, không hardcode model mà chưa xác minh.
- Nếu Azure adapter vẫn có giá trị, giữ tương thích theo code hiện tại, nhưng **mặc định VLM cho catalogue phải là OpenAI direct**, không phải Azure hoặc FPT.
- Image-generation của lookbook phải được route qua **OpenAI direct** khi team cần tiêu credit BTC; nếu thiếu image model/key thì fail có code, không sinh SVG/mock.
- Các test cũ kỳ vọng `503` khi Azure missing config cần cập nhật phù hợp với **OpenAI direct required** mà vẫn bảo toàn tinh thần test unavailable.

---

## 4. P0-B: IMAGE ANALYST BACKEND — UPLOAD → ANALYSIS DRAFT → COMMIT

**Nguyên tắc:** Ưu tiên một ảnh chứa một component và dữ liệu thật. Không đánh dấu multi-item segmentation đã xong khi chỉ có mô tả VLM.

**Công việc phải làm (BE):**

1. Tái dùng `POST /v1/assets`, storage, metadata và ownership; kiểm tra content type từ bytes, giới hạn kích thước, không trả public raw image.
2. Tái dùng `POST /v1/assets/{asset_id}/catalogize` + job; chọn VLM (OpenAI hoặc explicit FPT) như mục 3.
3. **Thêm persistence cho candidate draft** trước khi user quyết định lưu tủ đồ. Nếu hiện worker tự tạo `garments`, sửa flow để không commit item khi chưa duyệt, hoặc tạo một compatibility transition an toàn và có test. Đây là thay đổi nghiệp vụ **phải công bố** nếu ảnh hưởng client cũ.
4. Đề xuất các API **DESIGN** (chỉ nếu chưa có API tương đương, chọn naming nhất quán với repo):

```http
GET   /v1/assets/{asset_id}/candidates
PATCH /v1/candidates/{candidate_id}
POST  /v1/assets/{asset_id}/commit
```

Ví dụ commit (DESIGN): `{"candidate_ids":["<id-1>","<id-2>"]}`. Không lấy candidate ID từ người dùng khác. Xác nhận commit lần hai phải không tạo bản ghi trùng (idempotent hoặc 409 có lý do rõ).

5. Draft lưu status, owner, asset_id, normalized attributes, sửa đổi của người dùng, timestamps. Giữ `media_assets`/`garments` hiện có; tạo Alembic migration **mới**, không viết lại initial migration đã phát hành.
6. Candidate ở draft sau khi refresh/re-login vẫn truy xuất được; chỉnh nhãn không gọi lại VLM, không đốt token vô ích.
7. Nếu ảnh không liên quan (sách, báo, nội dung không phù hợp) thì xử lý an toàn, thông báo không có component hợp lệ; không ghi thành garment.

**P0 Acceptance:** tải ảnh thật → catalogue job chạy VLM → candidates có thể sửa/lưu DB → commit garment một lần → `GET /v1/garments` hiển thị đúng owner → reload không mất. Chạy được cả OpenAI default và FPT explicit khi có key.

**P1 (không chặn release):** bóc tách nhiều vật thể trong một ảnh, nhiều ảnh, image search bên ngoài, nhận diện pose/background; riêng segmentation yêu cầu xác minh ảnh/crop/object mapping chứ không suy từ mô tả VLM là đủ.

---

## 5. P0-C: WARDROBE MANAGER BACKEND

**Tái dùng các route hiện có, chỉ bổ sung khi chưa đáp ứng:**

- `GET /v1/garments`: thực sự trả các garment đã commit, đúng owner, không trả draft chưa duyệt.
- `PATCH /v1/garments/{garment_id}`: validate và persist metadata (`category`, `color`, `material`, `season`, `tags`); không cho sửa garment user khác.
- Filter category/tags có hiệu quả thật: nếu implement ở DB qua query params, thêm test tương thích query cũ. Pagination có thể P1 nếu tập nhỏ; không hardcode số đếm.
- Xác minh quan hệ asset → garment và signed URL, tránh N+1 không cần thiết.
- Tránh tạo bảng `wardrobeManagerDB` hay DB vật lý mới trong P0. Hợp đồng ba service của Scope 1 được giữ ở **module nghiệp vụ**, repo hiện dùng PostgreSQL chung. Đây là đề xuất architecture V2; nếu repo có quyết định khác, nêu ADR conflict thay vì âm thầm đổi.

**P0 Acceptance:** commit candidate xong thì list/get/update/filter qua HTTP đều nhất quán và tách biệt tenant.

---

## 6. P0-D: LOOKBOOK STYLIST BACKEND

**Phạm vi:** hoàn thiện luồng backend hiện có, không dựng lại entire Agent Framework.

1. `POST /v1/lookbooks` dùng garment IDs thuộc user, context/preset theme/occasion; server-side allowlist preset để demo không bị prompt tự do phá flow (đối chiếu schema thật trước khi thay đổi).
2. `ai_jobs(kind=lookbook)` xử lý async; OpenAI direct dùng cho AI planning/generation theo cấu hình của team, **không** gọi Qwen để generate image.
3. Persist `looks`, `look_items`, generated image S3 và approval; expose job status và ảnh ký URL.
4. `GET /v1/lookbooks/{lookbook_id}/looks` trả identity/garment IDs thật; accept/reject đúng `approval_id` và ownership. Like/dislike chỉ persist bằng route mới nếu nghiệp vụ yêu cầu; không đồng nhất like với approval nếu hai semantics khác nhau.
5. Sync lookbook status khi job `failed`; retry giới hạn, không kẹt `running` vô thời hạn; xử lý worker restart có kế hoạch khôi phục/reclaim.
6. **Reference garment image**: backend hiện đang render từ metadata; điều tra khả năng model OpenAI image hiện được cấu hình có nhận ảnh tham chiếu không. Nếu khả thi trong deadline, truyền ảnh thật vào generation/edit request. Nếu chưa khả thi, báo giới hạn fidelity rõ trong handoff, **không khẳng định feature đã hoàn tất**.
7. **Không bắt buộc** chỉnh look trước/sau, voice, VTON, Google image search, Social, Cmax auto-scoring ở P0.

**P0 Acceptance:** garment IDs từ tủ người dùng → tạo job → render ảnh thật với OpenAI → lưu và trả look → accept/reject có persistence → trạng thái và owner đúng → lỗi OpenAI không tạo success giả.

---

## 7. P0-E: SECURITY, OPERATIONS, RUNTIME VERIFICATION (BACKEND ONLY)

### Bảo mật

- JWT/permission với mọi asset, candidate, garment, job, lookbook, look và approval; tránh IDOR với signed URL.
- Consent `ai_processing` tại HTTP **và trước external inference trong worker**; nếu rút consent, không gửi ảnh từ queued jobs. Ghi rõ limitation với job đang thực thi; không hứa xóa dữ liệu bên provider nếu API không hỗ trợ.
- Log IDs/status/model/provider/latency, không log key, raw image, Base64 data URI, full prompt có PII hoặc signed URL.
- Cấu hình S3/RustFS private, URL ký hết hạn; lỗi upstream redact khi trả HTTP.
- `/mock` chỉ dùng test; deploy không public nếu chưa có bảo vệ, `ENABLE_API_MOCK_UI=false` trong môi trường không dùng kiểm thử.

### Deployment và lệnh kiểm thử

Tối thiểu chuẩn bị `.env.example`/README chạy local, worker cùng env, migration và test. Nếu repo đã có Dockerfile/Compose thì chỉ sửa cần thiết; nếu chưa có thì thêm Compose nhỏ cho `api`, `worker`, `postgres` và storage nếu cần, không tách thành dự án infra lớn. AWS deployment thực tế chỉ thực hiện khi role/quota/credential hợp lệ.

```bash
python -m pip install -e '.[dev]'
alembic upgrade head
python -m pytest -q
ruff check src tests migrations
ruff format --check src tests migrations
# Chạy với DB/storage thật (khi cấu hình đầy đủ)
uvicorn garblo.main:app --reload
# terminal khác
garblo-worker
```

Test phải có unit/mock và integration PostgreSQL thực (nếu có DB test), không phụ thuộc key thật cho CI. Live smoke test FPT/OpenAI **chỉ** khi có key được cấp, có consent ảnh test, mạng hoạt động; không tự ghi PASS cho test đã skip.

---

## 8. TEST MATRIX — AGENT PHẢI IMPLEMENT

| ID | Priority | Case | Expected |
|---|---|---|---|
| BE-01 | P0 | Không có FPT key, không gửi provider | OpenAI direct được chọn |
| BE-02 | P0 | Có FPT key, không gửi provider | **Vẫn OpenAI**, zero FPT calls |
| BE-03 | P0 | Có FPT key, `vlm_provider=fpt` | Đúng endpoint/model FPT, đúng image data URI |
| BE-04 | P0 | `vlm_provider=fpt`, thiếu key | 503, chưa enqueue, không fallback |
| BE-05 | P0 | Provider khác `openai`,`fpt` | 422 |
| BE-06 | P0 | FPT HTTP 200 envelope `data.choices` | Parse thành canonical schema |
| BE-07 | P0 | FPT HTTP 200 response root `choices` | Parse thành canonical schema |
| BE-08 | P0 | FPT HTTP 200 nhưng app `code` báo lỗi | Fail, không commit garment |
| BE-09 | P0 | Model trả malformed JSON / thiếu field | Failed / validation error, không fake garment |
| BE-10 | P0 | FPT 401/403 | Error được redact, không retry mãi, không gọi OpenAI |
| BE-11 | P0 | OpenAI timeout/429 | Retry bounded trên OpenAI, không gọi FPT |
| BE-12 | P0 | FPT 429/timeout | Retry bounded trên FPT, không gọi OpenAI |
| BE-13 | P0 | Đồng thời 2 job OpenAI/FPT | Không cross-contaminate provider |
| BE-14 | P0 | Retry FPT failed | Provider/model vẫn là snapshot FPT |
| BE-15 | P0 | Thêm key `.env`, restart API/worker | FPT available, không sửa source/rebuild |
| BE-16 | P0 | Upload thật + draft edit + refresh + commit | Dữ liệu bền, không phân tích lại khi edit |
| BE-17 | P0 | Commit hai lần | Không duplicate garments |
| BE-18 | P0 | Ảnh không chứa quần áo | Không tạo garment sai |
| BE-19 | P0 | User B đọc/sửa asset/candidate của user A | Không được phép; không lộ URL/ảnh |
| BE-20 | P0 | Rút consent trước worker call | Không inference, job trạng thái rõ |
| BE-21 | P0 | Lookbook generation với FPT key có sẵn | Image generation vẫn gọi OpenAI |
| BE-22 | P0 | Lookbook accept/reject | DB state và API response nhất quán |
| BE-23 | P0 | Lookbook worker failed/terminated | Không trả fake success; có retry/recovery |
| BE-24 | P0 | Client cũ không gửi body catalogue hoặc `{}` | Backward compatible, OpenAI default |
| BE-25 | P1 | Multi-item segmentation | Chỉ ghi đạt nếu crop/object mapping chính xác |

**Live-provider evidence** (không mock): ghi ngày, model, endpoint, mã HTTP, job ID, trạng thái, latency, response schema được validate, ảnh thử đã xin phép. **Không** đưa token hoặc ảnh/base64 vào report.

---

## 9. GỢI Ý PHÂN NHÓM FILE/CHANGES (CHỈ LÀ STARTING POINT)

Các đường dẫn bên dưới phải được agent **xác minh** trong repo. Không tạo file trùng chức năng nếu hệ thống đã có equivalent.

```text
src/garblo/
  config.py               # CHG: default provider OpenAI, optional FPT key/model/url
  ai.py                   # CHG: adapter OpenAI direct; delegate to provider registry
  ai_providers/           # NEW IF USEFUL: contract, openai_direct, fpt_qwen, normalizer
  api/routes.py           # CHG: optional vlm_provider, draft APIs, auth checks
  schemas.py              # CHG: request/response canonical schemas
  worker.py               # CHG: provider pinned per job, error handling, consent, retry
  models.py               # CHG only if necessary: draft/item relationships, metadata
  storage.py              # REUSE: image bytes from private storage
  main.py                 # REUSE: existing app
migrations/versions/      # NEW additive migration; DO NOT rewrite deployed migration
.env.example              # CHG: public example placeholders, no secret
README.md                 # CHG: setup, provider usage, errors, smoke test
Dockerfile/compose.yaml   # NEW IF MISSING/NECESSARY: API, worker, postgres

tests/
  test_ai_provider_selection.py
  test_fpt_qwen_adapter.py
  test_catalogue_draft_commit.py
  test_worker_provider_retry.py
  test_wardrobe_ownership.py
  test_lookbook_pipeline.py
```

### API hand-off FE team (không sửa FE)

Sau khi implementation, bàn giao **API được xác minh** với request/response/error samples. Ví dụ các request dự kiến:

```http
POST /v1/assets/{asset_id}/catalogize
Authorization: Bearer <jwt>
Content-Type: application/json

{}
```

→ OpenAI default.

```http
POST /v1/assets/{asset_id}/catalogize
Authorization: Bearer <jwt>
Content-Type: application/json

{"vlm_provider":"fpt"}
```

→ FPT Qwen nếu FPT key có trong worker/API.

```http
GET /v1/jobs/{job_id}
Authorization: Bearer <jwt>
```

→ Poll đúng job status, nhận result/candidate IDs hoặc domain error khi kết thúc.

---

## 10. THỨ TỰ THỰC THI CHO AGENT — KHÔNG DỪNG Ở PLANNING

**Stage 0 — Audit:** Xem source/working tree, vẽ ra call chain thực tế trong 10–20 dòng ghi chú, nêu khác biệt với báo cáo AS-IS.

**Stage 1 — Core providers (P0/GATE):** `.env.example` → OpenAI direct adapter → FPT adapter → optional request selection → pinned job metadata → response normalize/errors → tests. Đây là phần có ROI cao nhất và không phụ thuộc FE.

**Stage 2 — Image flow:** Upload → analyze → draft persisted → edit → commit (nhất quán DB/storage). Có test auth/consent/idempotency.

**Stage 3 — Wardrobe + lookbook:** Endpoints đúng source identity, filter/metadata, preset-safe lookbook, worker/job/approval, OpenAI image generation.

**Stage 4 — Integration/ops:** Migration PostgreSQL, API + worker cùng env, README/curl, tests/lint, Docker packaging tối thiểu nếu thiếu, live smoke khi key hợp lệ.

Trong trường hợp thiếu thời gian, **ưu tiên Stage 1 hoàn chỉnh và Stage 2 đi được một ảnh đơn** thay vì làm dở mọi nhánh. Với module chưa hoàn thiện, report đúng `PARTIAL`/`BLOCKED`, không claim đạt DoD.

---

## 11. DEFINITION OF DONE / FINAL REPORT TEMPLATE

Một stage chỉ được báo `DONE` khi có mã nguồn và test tương ứng, không chỉ có stub/interface.

### DoD P0

- [ ] Dùng OpenAI direct key làm VLM **mặc định**, kể cả khi FPT key tồn tại.
- [ ] Chỉ thêm `FPT_VLM_API_KEY` trong `.env` + restart là có thể **chọn** FPT Qwen mà không sửa source/config khác.
- [ ] FPT gọi đúng endpoint `https://mkp-api.fptcloud.com/v1/chat/completions`, model `Qwen2.5-VL-7B-Instruct`, ảnh thật dùng data URI Base64 và JSON output được normalize.
- [ ] Không silent cross-provider fallback; retry giữ provider/model job gốc; 401/429/timeout/invalid schema có trạng thái rõ.
- [ ] Luồng upload → catalogue → draft → user edit → commit → wardrobe lưu thật, quyền sở hữu/consent/idempotency được kiểm thử.
- [ ] Lookbook gen ảnh vẫn dùng OpenAI Images API; approval + job status persist, không trả success giả.
- [ ] Migration PostgreSQL up và relevant tests/lint pass; kết quả được trích từ lệnh thực chạy.
- [ ] `.env.example`, README và curl hand-off cho FE/QA có thể sử dụng; không có key thật trong git/log.
- [ ] Mọi task không kiểm chứng được ghi chính xác `BLOCKED` với lý do (thiếu key/quyền/network/service).

### Định dạng báo cáo cuối Agent phải trả

```markdown
# Garblo BE Implementation Report

## 1. Baseline
- Repo/branch/commit/working-tree:
- Call chain AS-IS (file:line):
- Điểm khác với hand-off:

## 2. Changes implemented
| Task ID | Status | Files/lines | Behavior | Test evidence |

## 3. API contract thực tế
- POST /catalogize: old/new request schemas, default behavior
- GET /jobs: shape, errors/status
- Draft/commit APIs: shape, auth, idempotency
- Garment/lookbook/approval APIs: behaviors

## 4. Tests executed
| Command / Case | Result | Evidence / blocker |
- Tách unit mock, integration DB, live OpenAI, live FPT.

## 5. Configuration & secrets
- .env.example (chỉ key names và giá trị placeholder)
- Cách restart API/worker để nhận key
- Cách chọn fpt và cách giữ OpenAI default

## 6. Known limitations & follow-ups
- P0 còn thiếu (nếu có), lý do và lệnh tái hiện
- P1 không thực hiện
```

**Cấm báo cáo “đã chạy end-to-end”, “đã test với FPT thật”, “đã deploy AWS”, “100% done” khi thiếu log/test quan sát tương ứng.**

---

## 12. NGUỒN THAM KHẢO/BÀN GIAO

- `Scope1.txt` — 3 must-have, thứ tự ưu tiên và deadline.
- `Backend-hiện-tại.txt` — báo cáo source backend ngày 30/10, dùng như baseline cần xác minh.
- `Garblo_PO_HandOff_Source_Implementation_OpenAI_Default_FPT_Qwen_VLM.md` — user stories và UAT PO.
- `Garblo_System_Architecture_V2_AWS_MVP.md` — kiến trúc logic AWS/OpenAI/FPT được đề xuất.
- `Technical_Guideline.pdf` — giới hạn hạ tầng BTC, credit, region và thời hạn.
- FPT Marketplace model card: https://marketplace.fptcloud.com/en/models/qwen2.5-vl-7b-instruct
- FPT VLM reference: https://ai-docs.fptcloud.com/api-reference/ai-marketplace/api-reference/api-integration-vision-language-model-md

**KẾT THÚC HAND-OFF. Agent bắt đầu bằng bước audit, sau đó sửa code thật và chứng minh bằng test.**
