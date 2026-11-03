# Stylist Agent một lượt

`POST /v1/stylist/messages`, JWT và `Idempotency-Key` (1–128 ký tự), trả
`202 {job_id,status}`. Consent `ai_processing`, `OPENAI_API_KEY` và
`STYLIST_INTENT_MODEL` bắt buộc. Thiếu consent trả 409, thiếu cấu hình trả 503;
không tạo job hoặc chuyển sang demo. Model dùng snapshot, không đổi giữa retry.

Tạo bộ phối:

```json
{"message":"Phối đồ đi làm thanh lịch","context":{"version":1,"event":{"id":"work"},"style":{"id":"elegant","source":"manual"}}}
```

`context` có thể bỏ: planner suy ra sự kiện/phong cách từ message hoặc hỏi lại.
Context tường minh có ưu tiên. Weather/transports không bắt buộc; location chỉ là
snapshot văn bản tùy chọn. Message 1–1000 ký tự, không cho chuỗi trắng.

Chỉnh bộ phối:

```json
{"message":"Đổi giày sang màu đen, giữ áo và quần","session_id":"<session>","outfit_id":"<outfit>","expected_revision":1,"locked_garment_ids":["<top>","<bottom>"],"excluded_garment_ids":[],"constraints":{"colors":["black"]}}
```

Ba field nguồn/revision phải có cùng nhau. Locks/excludes/constraints chỉ hỗ trợ
khi chỉnh một món. Mỗi request chỉ thực hiện một nghiệp vụ; yêu cầu nhiều thao tác
được hỏi lại. Chỉnh yêu cầu revision mới, món active owned và compatible role.
Explicit constraints/locks không bị model bỏ qua. Để tạo bộ mới từ màn kết quả,
quay về màn chọn bối cảnh.

Poll `GET /v1/jobs/{job_id}`. `output_ref` khi succeeded có một trong:

- `{kind:"outfits",session:SessionResponse}`: tối đa ba bộ, có thể trống với reason.
- `{kind:"updated_outfit",outfit:OutfitResponse}`: cùng outfit ID, revision tăng.
- `{kind:"clarification",question:string}`: chưa thay đổi dữ liệu; gửi request mới
  đầy đủ và key mới khi trả lời. Không có hidden conversation state.

Planner chỉ trả typed intent (`recommend`, `replace_item`, `clarify`). Backend
cấp metadata shortlist tối đa 48 món, không ảnh/object key, validate JSON/IDs rồi
gọi tối đa một service deterministic. Tạo bộ dùng message ưu tiên retrieval từ khóa;
ràng buộc bắt buộc từ văn bản tự do không được bảo đảm bằng schema. Dùng explicit
constraints cho chỉnh sửa cần chắc chắn. Điểm outfit là điểm quy tắc.

Một physical model call/attempt, SDK retry tắt. Tối đa số calls theo
`STYLIST_INTENT_MAX_CALLS` (mặc định 3), 1024 output tokens, reservation 16384,
token budget 50000, deadline 300s. Worker dùng lease fence, kiểm tra lại consent
và revision trước publish. Tạo/chỉnh và job success cùng transaction. Audit xem
`GET /v1/jobs/{id}/calls`; không ghi raw message/model response vào audit/log.
Payload private và câu hỏi làm rõ nằm trong job theo quyền owner hiện có.

Không cần migration cho stylist; DB phải có migrations hiện tại của recommendation
và retrieval. Restart cả API và worker để nhận code mới. Frontend `real` hiển thị
panel ở màn bối cảnh và kết quả, lưu operation theo API/user, tiếp tục poll sau reload.
Frontend `demo` giữ luồng local. Chưa có live eval chất lượng model hoặc quota USD/user.

Kiểm tra tự động dùng provider HTTP mock, không chứng minh đã gọi AI thật:

```bash
.venv/bin/python -m pytest -q tests/test_stylist.py tests/test_recommendation_worker.py tests/test_recommendation_conversation.py
```
