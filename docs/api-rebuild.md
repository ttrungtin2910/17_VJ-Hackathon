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

`DELETE /garments/{garment_id}` trả `204` sau khi xóa món và ảnh gốc/component.
Nếu món thuộc các bộ phối gợi ý, client phải hiển thị cảnh báo xóa cả bộ phối cùng
phản hồi và gửi `?delete_recommended_outfits=true`. Thiếu xác nhận (hoặc `false`)
trả `409` với `recommendation_delete_confirmation_required`, chưa xóa DB/storage.
Xóa cả outfit chứa món, cascade items/feedback; giữ phiên (có thể rỗng), các outfit
khác và audit job. Món thuộc `LookItem` hoặc có ảnh dùng chung vẫn bị chặn (`409`).
Backend flush các thay đổi DB trước khi xóa storage; lỗi ràng buộc còn lại trả
`409/garment_delete_conflict`. Lỗi storage trả `503/storage_delete_failed`, rollback
metadata và cho phép thử lại kể cả khi một phần hoặc toàn bộ object ảnh đã mất.

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
# Bổ sung contract local WSL (01/11/2026)

- Register giữ JSON hiện hữu, mật khẩu 12–128 ký tự và **tối đa 72 byte UTF-8**. Login vượt 72 byte trả 401 an toàn; không verify prefix bị bcrypt cắt, không thay hash cũ. User legacy dùng mật khẩu dài cần quy trình đổi mật khẩu ngoài slice này; không tự chuyển hash.
- `GET /v1/me/consents/latest?purpose=ai_processing`: Bearer, chỉ owner; trả `ConsentResponse | null`, bao gồm bản mới nhất **đã revoked**, không phục hồi grant cũ. Không tự cấp consent khi login/bootstrap.
- `DELETE /v1/consents/{id}` giữ 204 và gọi lặp được; ID của người khác trả 404.
- Policy `local-wsl-1` là nhãn thử nghiệm local: cho phép gửi ảnh đã chọn tới provider để phân loại quần áo; chưa phải chính sách production được phê duyệt. Thu hồi ngăn xử lý tại checkpoint tiếp theo, không cam kết hủy request upstream đang chạy, xóa dữ liệu provider hoặc tuyến tính hóa revoke/publish; URL ký đã cấp còn hiệu lực đến hết hạn.
- Lỗi validation 422 chỉ trả `type/loc/msg`; không phản chiếu password hay request input.

## Recovery upload/catalogue local

- `POST /v1/assets`: multipart hiện hữu, optional `Idempotency-Key` (1–128 ký tự `A-Za-z0-9._:-`); client real phải gửi. Cùng owner/key/asset_type/ảnh normalized trả lại asset ID cũ (201); khác nội dung trả 409 `idempotency_conflict`. Keyed upload không dùng checksum-dedup tùy chọn để gộp operation khác.
- Reservation commit trước network S3; lease 300 giây, cùng object key/asset ID qua retry. Pending còn lease trả 409 `upload_in_progress`; failed hoặc lease hết hạn có thể gửi lại **đúng file/key**. Upload cũ mất lease không publish (`upload_lease_lost`). Không giữ DB transaction qua S3 write, không hứa exactly-once object write; crash có thể còn orphan, không tự xóa.
- `GET /v1/assets/by-request/{request_key}`: owner-only `{request_key,status: pending|completed|failed,asset_id: string|null}`. Pending/failed không phải bằng chứng đã có asset. 404 nếu chưa có hoặc thuộc user khác.
- `GET /v1/assets/{asset_id}`: `AssetResponse` hiện hữu, owner-only.
- `GET /v1/assets/{asset_id}/jobs`: `JobResponse[]`, chỉ catalogue đúng owner/source; chưa enqueue trả `[]`. Nhiều job không phân biệt được trả 409 `ambiguous_catalogue_jobs`, không chọn newest và không expose `input_ref`.
- Mất response 202 sau catalogize: tra jobs đúng asset; retry failed dùng `POST /v1/jobs/{job_id}/retry` hiện hữu, không reupload hoặc enqueue job mới.

### Vòng đời ảnh đầu vào catalogue mới

Upload `garment_photo` mới có `metadata.retention_policy=temporary_catalogue_input`.
Job snapshot chính sách này; mỗi candidate thành công phải có `component_asset_id` riêng.
Sau commit kết quả, ảnh gốc được đưa vào hàng đợi `asset_cleanup`; worker xóa object
và thử lại khi storage lỗi, không gọi lại AI. Không cấp URL ảnh gốc từ lúc lên lịch dọn
(`/download` trả 410); bản ghi nguồn, ID và trạng thái review vẫn được giữ.
Ảnh gốc còn trong các lần retry có budget; hết attempts hoặc `no_items` cũng lên lịch dọn.
Upload/job cũ không có chính sách này không bị tự động dọn.

Xóa garment dùng ảnh component riêng, không chặn chỉ vì các món khác dùng chung nguồn.
Ảnh nguồn legacy chỉ được xóa theo thao tác xóa món khi không còn món hoặc nháp cần nó.
Candidate `status=deleted` vẫn phục vụ audit; frontend loại khỏi trình review.
