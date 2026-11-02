# Render từ ảnh garment trên nền backend cũ

Luồng candidate → review → commit → recommendation/feedback giữ nguyên. Frontend real có thể tạo ảnh cho một bộ phối, xem tiến độ, thử lại job lỗi và duyệt ảnh. Ảnh là người mẫu trưởng thành tổng hợp, không phải thử đồ trên người dùng. Demo không gọi tính năng AI này.

## Contract

- `GET /v1/recommendations/{session_id}/outfits/{outfit_id}/render`: `null` nếu revision hiện tại chưa có job; nếu có trả `job_id`, `status`, `error_code`, `generated_asset_id`, `approval_id`, `approval_status` (các trường chưa có giá trị là null).
- `POST` cùng đường dẫn, body `{"expected_revision":1}`: trả 202 với cùng response. Khóa user/session/outfit theo thứ tự hiện có; cùng outfit/revision dùng lại job, kể cả khi response trước bị mất. Revision cũ trả 409. Garment phải thuộc user và còn active. Không tạo job nếu thiếu consent hoặc cấu hình model.
- Poll GET khi queued/running; job lỗi dùng `POST /v1/jobs/{job_id}/retry` hiện có, giữ ID/attempt budget/consent checks. Hết budget không sinh job mới ngầm.
- Lấy ảnh bằng `GET /v1/assets/{generated_asset_id}/download` hiện có; frontend không lưu URL ký.
- Duyệt ảnh qua `POST /v1/approvals/{approval_id}/decision`, body `{"decision":"accepted"}` hoặc `rejected`. Đây là duyệt ảnh, không phải like/dislike recommendation và không xóa bytes ảnh khi từ chối.

GET/POST chỉ truy cập outfit của chính user. Job gắn outfit/revision trong `ai_jobs.input_ref`; không đổi schema/migration. PostgreSQL serialize việc tạo job bằng khóa user, tương tự service recommendation hiện hữu. Kiểm thử SQLite không chứng minh khóa concurrent PostgreSQL thực tế.

## Worker và provider

Job OpenAI lookbook mới snapshot `render_mode=garment_images`. Worker lấy ảnh component nếu có, nếu không lấy ảnh nguồn; kiểm owner và sanitize trước khi gửi. Planner metadata giữ preset allowlist, sau đó OpenAI `images.edit` nhận multipart ảnh. Prompt yêu cầu người mẫu trưởng thành tổng hợp, dùng quần áo tham chiếu. Chưa có segmentation thì một ảnh nguồn có thể chứa nhiều món, nên chất lượng giữ đúng từng món cần được đánh giá riêng.

Job legacy không có render_mode vẫn dùng cách generate cũ và model snapshot cũ. Không tự chuyển provider hoặc reset attempt budget. Giữ lease/consent trước upstream và fence trước lưu kết quả của worker cũ. Job mới từ chối cấu hình DALL-E; không fallback sang generate khi edit lỗi.

Cần `OPENAI_API_KEY`, `OPENAI_VLM_MODEL` và `OPENAI_IMAGE_MODEL` hỗ trợ image edit (chọn model đã được xác minh có quyền trong tài khoản). API + worker phải cùng bản code/cấu hình; restart/recreate cả hai sau cập nhật. Giữ PostgreSQL/S3 và chạy worker riêng. Không tự bật key hay gọi AI trả phí khi chạy test.

## Kiểm tra

```bash
python -m pytest -q -m 'not integration'
ruff check src tests migrations
ruff format --check src tests migrations
```

Test mới bao phủ multipart images.edit, source/component ảnh, owner, revision, consent/config, hồi phục cùng job, approval; test cũ bảo vệ snapshot legacy. Test dùng SQLite, fake storage/provider hoặc MockTransport; không đo chất lượng ảnh AI hay xác minh dịch vụ production. Cấu hình E2E mocked riêng của frontend: `npx playwright test --config playwright.render.config.ts`.
