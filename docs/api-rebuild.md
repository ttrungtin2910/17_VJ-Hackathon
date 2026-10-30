# Hợp đồng API rebuild — bàn giao FE/QA

Giữ prefix `/v1`, JWT Bearer, owner checks. ID trong ví dụ là placeholder; response
schema được test bằng mock provider, PostgreSQL/storage integration tách riêng.
Không sửa frontend trong nhiệm vụ này. OpenAPI tại `/openapi.json` là schema runtime.

## 1. Xác thực, consent và upload

```bash
export API=http://localhost:8000
# TOKEN là Garblo JWT lấy từ POST /v1/auth/login; không phải provider API key.
curl -X POST "$API/v1/consents" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"purpose":"ai_processing","policy_version":"v1"}'
curl -X POST "$API/v1/assets" -H "Authorization: Bearer $TOKEN" \
  -F 'file=@/path/to/consented-garment.png' -F 'asset_type=garment_photo'
```

Upload nhận JPEG/PNG/WebP thật; không tin MIME/header hoặc tên file. Trả `201` với
`id`, `mime_type`, checksum và status `uploaded`. MIME/checksum phản ánh bytes đã chuẩn hóa.
Không trả public URL. Quá 10 MiB mặc định → 413; ảnh lỗi/quá pixel/animated → 415.

## 2. Catalogue và poll job

```bash
# Không body cũng được; mặc định OpenAI dù FPT key tồn tại.
curl -X POST "$API/v1/assets/$ASSET_ID/catalogize" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}'
# HOẶC explicit FPT, không gọi cả hai request cho cùng asset:
curl -X POST "$API/v1/assets/$ASSET_ID/catalogize" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"vlm_provider":"fpt"}'
curl "$API/v1/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN"
```

Enqueue trả `202 {"job_id":"<id>","status":"queued"}`. Poll trả các field cũ và thêm
`model_provider`, `model`, `attempts`, `next_attempt_at`:

```json
{
  "id": "<job-id>", "job_type": "catalogue", "status": "succeeded",
  "output_ref": {
    "asset_id": "<asset-id>", "candidate_ids": ["<candidate-id>"],
    "result": "candidates", "provider": "fpt", "model": "Qwen2.5-VL-7B-Instruct",
    "latency_ms": 1200, "usage": {}
  },
  "error_code": null, "error_message": null,
  "model_provider": "fpt", "model": "Qwen2.5-VL-7B-Instruct",
  "attempts": 1, "next_attempt_at": null,
  "created_at": "<ISO-8601>", "completed_at": "<ISO-8601>"
}
```

Status không đổi: `queued`, `running`, `succeeded`, `failed`. Trong backoff job trở về
`queued`, có `next_attempt_at` và error code; chưa coi đó là lỗi terminal.
Ảnh không có quần áo trả `result=no_items`, `candidate_ids=[]`, không tạo garment.

**Breaking semantics có chủ đích:** không còn tự lưu wardrobe, không trả `garment_id`
ngay từ catalogue. FE phải dùng flow draft/commit dưới đây. Catalogue lặp → 409;
dùng candidate/job hiện có, không tốn token phân tích lại khi refresh/edit.

## 3. Draft và commit

```bash
curl "$API/v1/assets/$ASSET_ID/candidates" -H "Authorization: Bearer $TOKEN"
curl -X PATCH "$API/v1/candidates/$CANDIDATE_ID" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"category":"shirt","color":"red","tags":["office"]}'
curl -X POST "$API/v1/assets/$ASSET_ID/commit" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"candidate_ids":["<candidate-id>"]}'
```

Candidate response:

```json
{
  "id":"<candidate-id>", "asset_id":"<asset-id>", "job_id":"<job-id>",
  "status":"draft", "garment_id":null,
  "attributes":{"category":"shirt","color":"red","material":null,"season":null,"tags":["office"]},
  "created_at":"<ISO-8601>", "updated_at":"<ISO-8601>"
}
```

- PATCH chỉ cập nhật field gửi lên, không gọi AI. Field không biết để `null`, tags là array,
  tối đa 20 phần tử, mỗi chuỗi 1–80 ký tự; không gửi `tags:null`.
- Commit trả `200 {"garment_ids":["<garment-id>"]}`; lặp hoặc gửi đồng thời vẫn cùng garment.
- Candidate phải thuộc đúng user **và** asset. Không được sửa candidate đã committed (409);
  khi đó PATCH garment. Draft tồn tại sau refresh/re-login.

## 4. Wardrobe và ảnh private

```bash
curl "$API/v1/garments?category=shirt&tags=office" -H "Authorization: Bearer $TOKEN"
curl "$API/v1/garments/$GARMENT_ID" -H "Authorization: Bearer $TOKEN"
curl -X PATCH "$API/v1/garments/$GARMENT_ID" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"material":null,"tags":["office","summer"]}'
curl "$API/v1/assets/$ASSET_ID/download" -H "Authorization: Bearer $TOKEN"
```

`GET /garments` vẫn trả array, không đổi sang pagination envelope. Nhiều `tags=` có semantics
**AND**. Draft không xuất hiện trong wardrobe. Download trả `{url, expires_in_seconds}`;
URL là bearer capability có hạn dùng, không log/share. Garment dùng `source_asset_id` để xin ảnh.

## 5. Lookbook, approval và feedback

```bash
curl -X POST "$API/v1/lookbooks" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Đi làm","garment_ids":["<garment-id>"],"preset_id":"office"}'
curl "$API/v1/jobs/$JOB_ID" -H "Authorization: Bearer $TOKEN"
curl "$API/v1/lookbooks" -H "Authorization: Bearer $TOKEN"
curl "$API/v1/lookbooks/$LOOKBOOK_ID/looks" -H "Authorization: Bearer $TOKEN"
curl -X POST "$API/v1/approvals/$APPROVAL_ID/decision" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"decision":"accepted"}'
curl -X POST "$API/v1/looks/$LOOK_ID/feedback" -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"signal":"like"}'
```

- Presets: `everyday` (default), `office`, `weekend`, `evening`.
- Field cũ `theme`/`occasion` chỉ hỗ trợ allowlist/aliases trong `src/garblo/presets.py`;
  các field cung cấp cùng lúc phải map cùng preset. Free instruction, pose/background → 422.
- Generation luôn OpenAI, không có `vlm_provider` cho lookbook.
- Job thành công trả `lookbook_id`, `look_id`, `generated_asset_id`, `approval_id`.
  Look response giữ field cũ, thêm `garment_ids`, `approval_id`.
- Dùng `generated_asset_id` với download API để lấy signed URL.
- Preview chưa được accepted. Approval dùng `accepted`/`rejected`, quyết định lần hai → 409.
- Feedback dùng `like`/`dislike`, trả 201 `{id,look_id,signal}`; không thay approval status.
- Variation route hiện trả 422 cho prompt tự do; chưa hỗ trợ chỉnh ảnh/VTON/reference fidelity.

## 6. Errors và retry

| Tình huống | HTTP / job error |
|---|---|
| Provider config thiếu | 503 `detail.code=provider_not_configured`, chưa enqueue |
| Provider không hợp lệ | 422 validation |
| Không sở hữu resource | 404, không cấp signed URL |
| Thiếu consent tại HTTP | 409 |
| Consent rút trước inference | Job failed `consent_revoked`, không inference |
| Upstream 401/403 | Job failed `provider_auth_error`, không auto retry |
| 429/5xx/timeout | `provider_transient_error`, backoff có giới hạn, đúng provider |
| JSON/schema sai sau repair tối đa một lần | Job failed `invalid_model_output` |
| Hết attempts | HTTP retry 409 `retry_limit_exceeded` |

```bash
curl -X POST "$API/v1/jobs/$JOB_ID/retry" -H "Authorization: Bearer $TOKEN"
```

Retry chỉ nhận job failed, còn consent/config và còn budget; trả 202 với **cùng job ID**.
Provider/model giữ nguyên snapshot. Error thông thường cũ có `detail` dạng chuỗi; error provider
mới dùng `{detail:{code,message}}`. FE nên đọc cả hai, không hiển thị raw upstream body.

Live QA phải ghi ngày/model/endpoint/HTTP/job ID/latency, dùng ảnh được phép xử lý;
không đưa key, ảnh, Base64 hoặc signed URL vào báo cáo.
