# Nghiệm thu kỹ thuật Chặng A — 31/10/2026

## Gate trước khi triển khai Chặng B

Kết quả thực chạy tại backend, sau sửa feedback review độc lập:

```text
python -m pytest -q
249 passed, 1 warning in 68.72s

ruff check src tests migrations
All checks passed!

ruff format --check src tests migrations
49 files already formatted
```

Không skip. Warning từ `passlib` dùng `crypt` deprecated của Python 3.12.
Python/pytest/Ruff chạy trong `/tmp/opencode/garblo-venv/`.

## Tài nguyên test và bằng chứng

- PostgreSQL 16.6 riêng: container `garblo-agent-postgres-test`, host port 25433,
  DB `garblo_agent_test`; từng integration test tạo/migrate/dọn schema UUID riêng.
- S3-compatible MinIO riêng: `garblo-agent-s3-test`, host port 29000;
  storage integration tạo/dọn bucket UUID.
- API và worker là hai process local riêng, DB `garblo_runtime_test`, bucket
  `garblo-agent-runtime-test`, HTTP `127.0.0.1:28080`; không có AI keys.
- Env kiểm thử: `GARBLO_TEST_DATABASE_URL`, `GARBLO_TEST_S3_ENDPOINT_URL`,
  `GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY`, `GARBLO_TEST_API_URL`.
- Migration mới: `20261030_0003`. Không chạy migration vào DB ứng dụng/production.

| Luồng | Bằng chứng test |
|---|---|
| Single/multi component, null/unknown, versioned prompt/schema, native JSON schema optional | `tests/test_catalogue_v2_provider.py` |
| Draft không tự thành garment; sửa/reject/restore, partial/idempotent commit, ownership | `tests/test_catalogue_pipeline.py`, `tests/test_catalogue_draft_commit.py` |
| Reservation token/call không reset; deadline bền qua retry; audit repair/validation | `tests/test_catalogue_pipeline.py` |
| Screening relevance, bounded escalation, segmentation HTTP contract và masks | `tests/test_catalogue_stages.py` |
| Component/mask lineage qua worker, vẫn chờ human review | `test_optional_stages_persist_segment_and_mask_lineage` |
| Upgrade giữ wardrobe/draft legacy; concurrent commit/detail PATCH; timezone deadline UTC/+7 | `tests/test_postgres_integration.py` |
| Storage thật; upload/private download/thiếu provider config qua HTTP thật | `tests/test_storage_integration.py`, `tests/test_runtime_smoke.py` |

## Feedback đã khắc phục

1. Reservation bền trước physical call; thiếu usage không tự hoàn ngân sách.
2. `execution_deadline_at` bắt đầu từ call đầu tiên và không reset qua retry/reclaim.
3. Khóa row khi merge garment details tránh lost update.
4. Audit tách transport status với validation status, đánh dấu repair kể cả truncation.
5. Chuyển timezone bằng `astimezone`, không đổi nhãn datetime aware.
6. Provider refusal fail an toàn, không repair để vượt từ chối.
7. Các cột JSON mới dùng JSONB trên PostgreSQL, JSON trên SQLite.

## Giới hạn của gate

Đây là nghiệm thu **backend implementation + infrastructure test**, không phải nghiệm thu
accuracy của VLM, segmentation thực tế, UI tích hợp hoặc production readiness.

- AI inference và segmentation response được mock trong tests. Không gọi API tính phí.
- Screening dùng typed catalogue analysis để phân image relevance; không phải moderation.
- Không có confidence đã calibration, giá USD/image hay accuracy được bịa. Metrics trả null
  cho chi phí/accuracy chưa đo; latency metrics là physical call, không phải queue latency.
- Token reservation là upper estimate do operator cấu hình; input token do provider tính có
  thể vượt estimate. Call/output caps chặn request tiếp theo, không thể thu hồi phí đã phát sinh.
- S3/DB không có distributed transaction; object orphan cần đối soát, không xóa prefix hàng loạt.
- Retention/deletion production và live provider/capability smoke cần được xác minh trước release
  có dữ liệu người dùng. Chưa hứa xóa dữ liệu đã gửi tới provider.
- Frontend vẫn demo, chưa thay adapter HTTP. Không phát triển thêm image generation/VTON.

**Kết luận:** gate Chặng A cho phép tiếp tục triển khai backend Chặng B; không cấp quyền
triển khai production hoặc xác nhận chất lượng AI thật.
