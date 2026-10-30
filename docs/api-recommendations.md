# API recommendation — Chặng B

Prefix `/v1`, JWT Bearer và ownership bắt buộc. Migrations `0003` → `20261031_0004`
additive, recommendation tách khỏi Look/Lookbook/generated assets/approval hiện có.

## 1. Tạo và đọc phiên

```bash
curl -X POST "$API/v1/recommendations" \
  -H "Authorization: Bearer $TOKEN" -H 'Idempotency-Key: <unique-request-key>' \
  -H 'Content-Type: application/json' \
  -d '{"context":{"version":1,"event":{"id":"work"},"style":{"id":"formal","source":"manual"},"weather":null,"transports":[]}}'
```

Trả `201 SessionResponse` gồm `id`, `context`, `location`, `engine_version`,
`weights_snapshot`, `outfits`, `reason`, `missing_categories`, `score_notice`, `created_at`.
Đọc `GET /recommendations/{session_id}`; list `GET /recommendations?limit=50&offset=0`
(limit 1–100). Không cần provider key hoặc external inference để phối đồ.

- Event/style bắt buộc; weather nullable, transports mặc định []. Các IDs giống frontend
  `src/data/contracts/types.ts`. Chọn `other` phải có `custom` không trống.
- `location` optional ở request root: chỉ snapshot text thủ công, chưa suy luận vị trí/thời tiết.
- Cùng user/key và cùng nội dung trả cùng session IDs, không phối lại. Key dùng cho nội dung
  khác trả 409. Replay đọc trạng thái hiện tại của cùng session, gồm edits/feedback nếu đã có.
- Tối đa 3 bộ, không bắt buộc đủ 3. Tủ thiếu đồ trả danh sách trống/lý do/nhóm đồ thiếu,
  không sinh item ngoài tủ hoặc shopping suggestion.
- Garment active và category hiểu được mới được dùng; category còn uncertainty không được
  dùng như category đã xác nhận. Mapping gồm category FE, English legacy và tiếng Việt.
- Template: top+bottom+shoes hoặc dress+shoes; có thể thêm outerwear khi cold/snowy.
  Chỉ loại theo thời tiết khi có cue cụ thể; unknown không bị suy diễn thành không phù hợp.

Mỗi outfit có `id`, `session_id`, `rank`, `revision`, `items`, `score`, `breakdown`,
`explanation`, `features_snapshot`, `feedback`. Mỗi `items[]` chứa `garment_id`, `role`,
snapshot metadata và source/component asset IDs. Xin URL ảnh qua download API hiện có.
Không có ảnh AI/try-on mới; frontend có thể dựng composition từ chính ảnh các món.

## 2. Ranking và personalization

Engine `rules-v1`: tối đa 8 món/role, 512 tổ hợp/template, 1024 tổ hợp tổng; thứ tự tie
theo IDs ổn định, Top 3 có diversity penalty. Score breakdown:
`context`, `style`, `color`, `weather`, `repetition`, `personalization`.

Điểm theo quy tắc **không phải confidence hoặc accuracy AI**. Unknown/uncertain không
được dùng làm bằng chứng đã xác nhận cho hard constraints màu/chất liệu.

Latest feedback tối đa 1000 outfits gần nhất của user, feature shrinkage và cap 3/feature,
cap tổng personalization 8. Session lưu weights snapshot; không tính lại phiên cũ khi có
feedback mới. Like không phải đã mặc: không thay wear_count/last_worn_at. Chưa dùng
StyleSignal legacy như dữ liệu đã học vì chưa có historical feature snapshot adapter.

## 3. Sửa đúng một component — deterministic

`POST /recommendations/{session_id}/outfits/{outfit_id}/edits`

```json
{
  "expected_revision": 1,
  "operation": "replace",
  "target_garment_id": "<current-id>",
  "replacement_garment_id": "<owned-active-compatible-id>",
  "locked_garment_ids": ["<keep-id>"],
  "excluded_garment_ids": [],
  "constraints": {"colors":["black"],"materials":[],"styles":[]}
}
```

Replacement optional: backend chọn deterministic trong tủ nếu bỏ field này. Không LLM,
không inference. Giữ nguyên các slot khác và IDs của item rows; chỉ tăng revision của outfit
đã sửa. Score tính lại theo weights snapshot của phiên, không học từ phản hồi mới ngầm.

Trước ghi phải kiểm tra lại mọi món thuộc user, active, role không bị đổi, không trùng ID,
đủ bộ, hợp context/constraints. Không đổi slot đã khóa hoặc loại món thuộc slot khác.
CAS revision + row locks bảo vệ cập nhật đồng thời. HTTP 409 khi revision/wardrobe thay đổi,
422 khi không có replacement hợp lệ hoặc yêu cầu mâu thuẫn; lỗi không ghi một phần.

## 4. Feedback

`PUT /recommendations/{session_id}/outfits/{outfit_id}/feedback`
với `{"signal":"like"}` hoặc `{"signal":"dislike"}`. Trả 200 `{outfit_id,signal}`.

Một latest record/user/outfit, retry cùng signal không tạo thêm mẫu. Flip đổi dấu trên
feature snapshot đã lưu, không nhân trọng số; sửa garment sau này không sửa lịch sử feedback.
Feedback không thay approval của lookbook cũ.

## 5. Hội thoại tùy chọn qua worker

`POST /recommendations/{session_id}/outfits/{outfit_id}/messages`, có `Idempotency-Key`.

```json
{
  "message":"Đổi giày sang màu đen, giữ nguyên áo và quần",
  "expected_revision":1,
  "locked_garment_ids":["<top-id>","<bottom-id>"],
  "excluded_garment_ids":[],
  "constraints":{"colors":["black"]}
}
```

`locked_garment_ids` bắt buộc gửi kể cả []; constraints là ràng buộc typed optional của
client, được giữ nguyên tới service, không cho LLM bỏ qua. Message 1–1000 ký tự, cấm trắng.

- Cần consent `ai_processing`, OpenAI key và `STYLIST_INTENT_MODEL` do operator xác minh.
  `STYLIST_INTENT_NATIVE_STRUCTURED_OUTPUT` chỉ bật khi đã xác minh support; Pydantic luôn
  validate lại. `CATALOGUE_SUPPORTS_TEMPERATURE=false` bỏ temperature khi model không hỗ trợ.
- Thiếu config 503, không enqueue; không âm thầm chuyển sang typed edit/demo/provider khác.
- Trả 202 `{job_id,status}`. Cùng key/body replay cùng job và status hiện tại, kể cả succeeded.
- Worker gửi bounded metadata tối đa 60 món active owned, không gửi ảnh/object keys. Message
  và metadata nằm trong user JSON; system prompt cố định, không tool execution.
- Model chỉ trả typed intent target/replacement/locks hoặc clarification. ID bịa/foreign,
  target bị khóa và schema sai bị chặn; không bypass validity/ranking service.
- `needs_clarification` là failed job cần người dùng làm rõ; không giả success.
- Một physical call/attempt, tổng tối đa 3 mặc định; 1024 output tokens, token reservation
  16384/call, observed+reserved budget 50000, deadline 300s từ call đầu. Retry giữ snapshot,
  không reset call budget/deadline. SDK retry tắt; retry transient do worker quản lý.
- Trước publish, recheck lease/consent và expected revision; outfit edit + job success cùng
  transaction. Worker cũ không overwrite; persist lỗi rollback outfit, audit call vẫn được giữ.

Poll `/jobs/{id}`; thành công `output_ref` là outfit response đã cập nhật. `/jobs/{id}/calls`
ghi stage `intent`, model/prompt version, usage, validation outcome và latency; không raw message
hay raw model response trong output/log. Raw message chỉ nằm trong private job input để replay.

## 6. Vận hành và giới hạn

- Chạy `alembic upgrade head`, restart **API và worker**. Vẫn cần worker process riêng cho AI.
- Production cần rate limit/quota theo user, retention/deletion policy, TLS/IAM và live eval
  trước release. Budget hiện theo job, không phải tổng hạn mức USD/user/ngày.
- Usage thiếu là unknown, không phải 0; reservation là estimate do operator chọn. SDK timeout
  là I/O timeout; deadline được recheck trước dispatch/publish, không thu hồi request đã gửi.
- Không hứa exactly-once billing. Không gọi AI thật trong bộ test; model semantics chưa được
  chứng minh chỉ bằng schema/ID validation. Dùng explicit constraints khi cần ràng buộc chắc.
- Frontend vẫn demo, HTTP adapter chưa tích hợp. Không nới preset/free-prompt rules của
  `/lookbooks` hoặc `/looks/{id}/variations` cũ.
- Rollback: chặn enqueue mới, drain jobs, rollback app phù hợp schema, giữ bảng additive và
  snapshots. Không downgrade/xóa sessions khi đang cần giữ dữ liệu.
