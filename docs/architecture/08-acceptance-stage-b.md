# Nghiệm thu backend Chặng B và regression A — 31/10/2026

## Kết quả

Chặng A đã qua gate riêng trước khi bắt đầu B: [249 tests](07-acceptance-stage-a.md).
Sau tích hợp B và sửa feedback/regression, chạy toàn bộ suite với đầy đủ env integration:

```text
python -m pytest -q
368 passed, 1 warning in 65.42s (0:01:05)

ruff check src tests migrations
All checks passed!

ruff format --check src tests migrations
63 files already formatted

git diff --check
(không có lỗi)
```

**Không có ca skip trong lần nghiệm thu tổng.** Warning duy nhất: `passlib` dùng `crypt`
deprecated trên Python 3.12; chưa migrate password hashing trong phạm vi này.

Lệnh thực dùng Python/Ruff trong `/tmp/opencode/garblo-venv/bin/`. Chạy ở `garblo-backend/`,
cấu hình đủ `GARBLO_TEST_DATABASE_URL`, `GARBLO_TEST_S3_ENDPOINT_URL`,
`GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY`, `GARBLO_TEST_API_URL`.
Không đưa credential ứng dụng/provider vào báo cáo. Tất cả credential ở test infrastructure
là credential test riêng do lượt làm việc này tạo.

## Phạm vi đã thực thi

| Hạng mục | Module / bằng chứng |
|---|---|
| Context versioned, category mapping Việt/Anh/FE, owned active garments | `recommendations/schemas.py`, `engine.py`, `service.py` |
| Bounded candidate generation, completeness/weather gates, scoring/diversity Top 3 | `recommendations/engine.py`, `tests/fixtures/outfit-quality.json` |
| Session/item/feature/weights snapshots, idempotent create | `recommendations/models.py`, migration `20261031_0004` |
| Typed edit đúng một slot, explicit locks/constraints, CAS revision, atomic rollback | `recommendations/service.py`, `tests/test_recommendations.py` |
| Latest idempotent feedback, bounded personalization, không tự tăng wear_count | `test_feedback_latest_upsert_bounded_snapshot_and_no_wear` |
| Optional conversational intent, strict JSON/allowlisted IDs, clarification/refusal | `recommendations/conversation.py`, `test_recommendation_conversation.py` |
| Worker consent/lease/deadline/call budgets, atomic edit + job success | `worker.py`, `tests/test_recommendation_worker.py` |
| PostgreSQL concurrent create/edit/feedback, async edit rollback/revision conflict | `tests/test_recommendation_integration.py` |
| HTTP runtime recommendation không có AI keys, không bịa outfit khi tủ trống | `test_recommendation_http_runtime_works_without_ai_keys` |

Engine không cần LLM để phối, ranking, edit typed hoặc học trọng số từ feedback. LLM chỉ
được bật riêng cho đọc ý định hội thoại; không tạo ảnh mới, không mở prompt tự do ở lookbook.
Input location chỉ được snapshot thủ công, chưa geocode hay suy luận thời tiết tự động.
StyleSignal legacy chưa được coi là feature history đã học; không dựng snapshot giả.

## Feedback/regression đã sửa trong lượt nghiệm thu

1. Segmentation stream nhỏ giọt: thay synchronous buffered iterator bằng HTTP async có
   `asyncio.timeout` bao phủ connect/headers/body; không gom đủ 64 KiB trước kiểm tra deadline.
   Có tests slow headers và one-byte trickle, hủy trước EOF/đầy buffer.
2. Role thay đổi ở wardrobe: chặn trường hợp đổi chéo top/bottom vẫn đủ tổng roles nhưng
   không còn đúng slot snapshot; trả 409 và giữ outfit cũ.
3. Timestamp: defaults mới lưu UTC, session response normalize UTC để replay/GET không đổi
   timezone giữa SQLite test và PostgreSQL.
4. Audit/replay hội thoại: trả status thật của job cũ, không báo queued khi đã succeeded.
5. Ràng buộc màu/chất liệu uncertain không được xem là bằng chứng đã xác nhận; constraints
   typed của client được giữ nguyên qua model intent tới deterministic edit service.
6. Screening/escalation model configs tách OpenAI/FPT; không gửi OpenAI model ID sang FPT.
7. Personalization chỉ đọc tối đa 1000 feedback gần nhất, latest signals và shrinkage/caps;
   không tăng trọng số bằng retry/flip cùng outfit.
8. Test PostgreSQL fixture reuse hoạt động cả khi chạy file riêng và toàn suite.

Reviewer sub-agent đã phản biện A/budget/fencing/stages. Các agent B bị giới hạn sử dụng
trước khi hoàn tất; main agent kiểm tra, hoàn thiện tích hợp và chạy nghiệm thu B. Đây không
phải chứng nhận Tech Lead/independent security sign-off cho production.

## Hạ tầng đã thực chạy

- PostgreSQL 16.6 container test riêng, DB `_test`, migration trong schema UUID riêng.
- S3-compatible MinIO container test riêng, storage integration trên bucket UUID.
- API và worker là hai process local riêng; runtime DB/bucket riêng không có AI keys.
- Migrations `0001` → `0004`, có test upgrade giữ garment/draft legacy và concurrency.
- Provider/segmentation/intent responses dùng doubles hoặc HTTP mock transport. Không gửi
  request AI tính phí, không chứng minh OpenAI/FPT/SAM hay provider deployment thật.
- Sau QA, process/container test do lượt làm việc tạo được dọn; DB/bucket ứng dụng không bị sửa.

## Runbook và điều kiện release

1. Review migration/config trong staging của đội, không tự chạy vào production.
2. Cài dev dependencies theo README; cấu hình DB/S3/JWT; `alembic upgrade head`.
3. Chạy API và `garblo-worker` ở hai process; thay env phải restart/recreate cả hai.
4. Deterministic recommendation chạy không cần provider key. Intent chỉ bật sau khi xác minh
   model/JSON capabilities/quyền truy cập và phê duyệt ngân sách.
5. Optional stages mặc định tắt; segmentation cần service/model thật và đánh giá mask chất lượng.
6. Rollback bằng chặn enqueue/drain rồi rollback app; giữ bảng/snapshots additive, không downgrade
   DB có dữ liệu cần giữ, không sửa snapshot provider/model/prompt của jobs cũ.

Contracts: [catalogue v2](../api-catalogue-v2.md), [recommendation](../api-recommendations.md).

**Chưa nghiệm thu:** live AI/segmentation accuracy, fashion quality do người đánh giá, chi phí
USD thực nghiệm, browser E2E/HTTP frontend, production quota/retention/deletion/IAM/TLS.
Các quy tắc/fixture quality không được trình bày như chỉ số accuracy hay confidence thật.

**Kết luận:** đã hoàn tất backend A → gate A → backend B → gate regression tổng. Có thể bàn
giao review/tích hợp frontend và live provider QA có phê duyệt; không đồng nghĩa đã deploy
production hay frontend đang dùng API thật.
