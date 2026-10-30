# API catalogue v2 — Chặng A

Prefix `/v1`, JWT/ownership/consent và request catalogue cũ không đổi. Không gọi thêm
inference khi đọc/sửa draft. Model IDs ở `.env.example` không mặc định là đã xác minh.

## Luồng chính

1. `POST /consents`, purpose `ai_processing`.
2. `POST /assets`: ảnh thật, bytes/MIME/checksum sau chuẩn hóa. Optional dedup chỉ cùng user
   và asset type khi `CATALOGUE_DEDUPLICATE_UPLOADS=true`; upload lặp trả asset hiện có.
3. `POST /assets/{id}/catalogize`: `{}` dùng OpenAI, `{"vlm_provider":"fpt"}` chọn FPT.
   Thiếu config trả 503 trước enqueue; asset đã có analysis trả 409, dùng job/draft hiện có.
4. `GET /jobs/{id}` → `output_ref.candidate_ids` (0–20). `no_items` không thêm wardrobe.
5. `GET /assets/{id}/candidates`, sửa/reject/restore rồi commit selection.
6. `POST /assets/{id}/commit` → `{ "garment_ids": [...] }`, cùng thứ tự IDs đã deduplicate.

## Candidate

Các field cũ được giữ. Field additive:

```json
{
  "schema_version": 2,
  "attributes": {"category":"áo","color":"xanh","material":null,"season":null,"tags":[]},
  "details": {
    "display_name":null,"subcategory":null,"pattern":null,"fit":null,
    "styles":[],"seasons":[],"uncertain_fields":["material","fit"]
  },
  "review": {"needs_review":true,"reason_codes":[],"edited_by_user":false},
  "component_asset_id": null
}
```

- Attributes cũ: cả năm field required trong AI output, nullable trừ tags.
- Details: field tùy chọn, unknown là null/[]; cấm extra fields và confidence số.
- `needs_review` không phải confidence. Tất cả component luôn cần người dùng commit.
- UUID candidate/garment do backend sinh; VLM không sinh persistent IDs.
- `original_attributes`/`review.original_details` phục vụ audit trước chỉnh sửa.

Ví dụ PATCH:

```json
{"color":"đen","details":{"display_name":"Áo đi làm","styles":["smart-casual"]}}
```

PATCH merge field gửi lên; không xóa pattern/styles không được gửi. Reject:
`{"status":"rejected"}`; khôi phục: `{"status":"draft"}`. `status:null` bị từ chối.
Candidate committed trả 409 khi sửa qua endpoint draft.

Commit từ chối selection có rejected candidate hoặc category null/rỗng/unknown/unclassified.
Validate toàn selection trước ghi. Commit một phần không tự reject phần còn lại.
Asset `partially_catalogued` khi còn draft và đã có món commit; `catalogued` khi không còn
draft, `rejected` nếu tất cả bị reject và không có món commit.

## Audit và budgets

`GET /jobs/{id}/calls`: call index, worker attempt, stage, provider/model, prompt/schema
version, reservation, usage, latency, error và validation outcome; không raw response/ảnh/key.

- `reserved`: request có thể đã gửi, kết quả chưa xác định; không đồng nghĩa chưa tính phí.
- Transport `succeeded` vẫn có thể `validation_status=invalid`; repair có stage `_repair`.
- Usage `{}` nghĩa chưa đo được, giữ reservation. Không tự quy thành token/cost 0.
- Reservation serialize bằng khóa job; tổng call và token budgets không reset qua HTTP retry.
- `execution_deadline_at` bền từ request đầu tiên, kể cả retry/reclaim/timezone khác UTC.
- Lỗi bổ sung: `model_call_budget_exceeded`, `token_budget_exceeded`, `job_deadline_exceeded`,
  `provider_refused`, `invalid_segmentation_output`. Refusal không repair để vượt từ chối.

`GET /me/ai-metrics` tổng hợp tối đa 1000 catalogue jobs gần nhất của user. Có physical-call
p50/p95, token counters, JSON validity, correction/escalation/retry rates. Chi phí USD và
accuracy chưa có dữ liệu đo trả null; latency không bao gồm queue wait.

## Optional stages và segmentation service

Các env optional xem `.env.example`; mặc định tắt. Screening/escalation dùng model đã xác
minh trên **cùng provider request đã chọn**. Snapshot pin các model/limits, không fallback.
Screening chỉ phân image relevance, dùng schema catalogue v2; không thay file validation
hoặc một safety moderation service. Escalation không bỏ bước human review.

Segmentation endpoint do operator cấu hình, không từ client/VLM. Service cần được bảo vệ
bằng private network/reverse proxy phù hợp. Request:

```json
{"model":"<verified-model>","image_base64":"<bytes>","mime_type":"image/png",
 "components":[{"candidate_index":0,"category":"áo"}]}
```

Response chỉ nhận inline bytes, không tải URL tùy ý:

```json
{"components":[{"candidate_index":0,"image_base64":"<component-bytes>","mask_base64":"<png-mask>"}]}
```

Phải trả đủ indices, không trùng; mask PNG cùng kích thước ảnh nguồn. Giới hạn response
32 MiB, từng ảnh theo upload/pixel limits. Không coi mask hợp lệ về bytes là đã đúng về
ngữ nghĩa. Worker lưu component/mask assets riêng, `parent_asset_id` trỏ ảnh gốc; component
metadata liên kết mask, candidate/garment trỏ `component_asset_id`. Khi không có service,
component dùng ảnh gốc và ID asset tách là null.

S3 writes không atomic cùng SQL. Claim có prefix riêng để worker cũ không overwrite; crash
có thể tạo orphan. Đối soát DB references trước dọn storage, không lifecycle-xóa cả prefix.

## Release gate

Backend/infrastructure gate xem `architecture/07-acceptance-stage-a.md`. Chưa có live VLM
hoặc segmentation accuracy gate, chưa tích hợp frontend. Production retention/deletion và
quyền xử lý dữ liệu provider cần được xác minh riêng; không hứa thu hồi request đã gửi.
