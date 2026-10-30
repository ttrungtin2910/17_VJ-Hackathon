# ADR-001 — Ưu tiên hai pipeline deterministic, không multi-agent cho MVP

- **Trạng thái:** Đề xuất thiết kế, chờ phê duyệt; không phải chứng nhận implementation hay nghiệm thu.
- **Ngày:** 30/10/2026.
- **Baseline:** `52346ec95f53bb29b28a8f48c62efbf8097ce2ab`, trước thay đổi A/B.
- **Phạm vi:** A Image-to-Component metadata → Human Review → Wardrobe; B recommendation từ tủ đồ thật sau nghiệm thu A. Không mở rộng VTON hoặc image generation trong quyết định recommendation.
- **Nguồn:** `docs/hand-off-image_to_wardobe.md:342-388`, `docs/prompt_improve-agent.md:21-29`, `docs/prompt_improve-agent.md:40`.

## Bối cảnh

Backend đã có FastAPI, PostgreSQL job queue, worker với lease/fencing/backoff và provider adapters; draft/commit tách khỏi analysis. Bằng chứng: `src/garblo/worker.py:88-162`, `180-207`, `266-296`; `src/garblo/api/routes.py:245-357`. Baseline chỉ cho tối đa một garment metadata (`src/garblo/ai_providers/contracts.py:25-28`). Lookbook tạo ảnh qua planner/image model (`src/garblo/ai_providers/openai_direct.py:100-152`), không có recommendation ranker Top 3 được tìm thấy.

Yêu cầu sản phẩm cần IDs hợp lệ thuộc tủ người dùng, human review, bounded retries/cost, recommendation có thể kiểm chứng, chỉnh component riêng và feedback cá nhân hóa. Chỉ gọi tuần tự vài model không phải lý do đủ để thêm multi-agent/framework.

## Quyết định đề xuất

### 1. Pipeline A: deterministic orchestration, inference ở ranh giới hẹp

- Dùng FastAPI → PostgreSQL job → worker hiện hữu; tách service/helper khi cần, không thay hạ tầng queue.
- Analysis v2 trả nhiều **metadata candidates**, strict typed details/uncertainty và bounds. Không suy từ metadata sang crop/mask thật.
- Local validation/quality gate quyết định repair/review/escalation theo policy versioned; model không tự chọn tool/provider/persist.
- Human review sửa/reject/restore; explicit commit có allowlisted mapping, ownership, locks, validation toàn batch, partial aggregate state và idempotency.
- Ghi audit/reserve quota cho **mọi physical call**, gồm failed/repair/retry. Version snapshots bất biến xuyên retry, không implicit provider fallback hoặc tự nâng model/prompt/schema job cũ.
- Screening/escalation/segmentation P1 disabled cho tới khi có route/config/capability verification, consent, budget và người duyệt. Không hardcode model ID từ tài liệu nguồn.

### 2. Pipeline B: pure recommendation core, chỉ sau nghiệm thu A

- Context typed + own active confirmed wardrobe → bounded slot pools/templates → bounded candidate generation → deterministic validity/scoring/diversity → tối đa 3 outfits với stable tie-break và structured reasons.
- Typed edits với locked slots và server-side validation; optional LLM chỉ dịch intent/giải thích, không vượt hard constraints hay tự chọn garment ngoài whitelist.
- Feedback idempotent, one-time apply, bounded/decayed/versioned preference deltas, opt-out/reset. Approval khác like/dislike.
- Recommendation không cần image model/key hoặc imagegen job. Tạo ảnh là action riêng, không chạy ngầm khi rank/edit/feedback.

### 3. Tái lập có phạm vi

Deterministic workflow không có nghĩa output VLM deterministic tuyệt đối. Tái lập recommendation yêu cầu cùng wardrobe/context/preference/taxonomy/ranking snapshots. Lưu version mà không giữ implementation/prompt phù hợp không đáp ứng replay.

## Các phương án đã cân nhắc

| Tiêu chí | A — workflow deterministic trên worker hiện hữu (chọn) | B — agent orchestrator nhiều tools/agents | C — một LLM quyết định toàn bộ từ ảnh đến phối đồ |
|---|---|---|---|
| Complexity | Thêm schema/state/call lifecycle trong stack hiện tại | Thêm planner/tool permissions/state graph/checkpoint/framework và failure modes | Bề ngoài ít code, logic nghiệp vụ bị giấu trong prompt |
| Latency | Số stage/call có trần, skip stage qua policy explicit | Planner/critic/tool loop thêm latency khó dự đoán nếu không bound | Có thể ít call ban đầu nhưng repair/retry không rõ |
| Cost | Physical-call ledger và reservation enforce được | Vẫn cần toàn bộ ledger/caps; planning overhead thêm call | Khó gán chi phí/quality từng bước, dễ retry toàn prompt |
| Reliability | State machine, transaction, idempotency/fencing explicit | Tool loop/replanning thêm nhánh; external IO vẫn không exactly-once | Có thể hallucinate IDs, state và approval |
| Observability | Stage/outcome/version/call rõ | Tool traces phong phú nhưng cần scrub PII, attribution nhiều tầng | Một response khó phân rã nguyên nhân |
| Testability | Unit pure logic, adapter mocks, API/DB concurrency tests | Cần kiểm thử planner/tool policies và graph replay ngoài business rules | Prompt regression phụ thuộc model, hard rules không chắc |
| Extensibility | Stage adapters/config flags đủ cho screening/escalation/segmentation | Tốt khi có branching/tool planning thật sự phức tạp | Prompt tăng dài, thay đổi một yêu cầu ảnh hưởng toàn bộ |
| Compatibility | Tái sử dụng PostgreSQL jobs/providers/review | Cần adapter queue/checkpoint/state và migration thêm | Dễ bỏ qua contracts/worker/consent đang có |
| Safety/ownership | Server hard gates, model chỉ trả data | Phải hạn chế mọi tool, không trao persist/query tùy ý | Model không được làm authority để xác nhận owner/active |

Không chọn orchestrator agent ở MVP vì không có bằng chứng đo được rằng graph/planner động giải quyết vấn đề còn lại hiệu quả hơn state machine. Không chọn monolithic LLM vì vi phạm ưu tiên deterministic validity/filter/ranking và khó đảm bảo tủ đồ thật.

## Hệ quả

### Lợi ích kỳ vọng — chưa phải số đo

- Thay đổi nhỏ hơn trên nền có sẵn; dễ kiểm thử bất biến dữ liệu, budget và ownership.
- Human review rõ ràng, tránh tự thêm garment chưa xác nhận và tránh nhầm metadata với segmentation.
- Ranking/edits/feedback không bị imagegen availability/chi phí chi phối.
- Traceability theo job/stage/physical call và version giúp điều tra lỗi, không cần lưu raw provider body mặc định.

### Chi phí và hạn chế

- Cần viết state/quality policy và version registry cẩn thận; strict taxonomy có thể yêu cầu user sửa nhiều hơn.
- Ledger/reservation transaction thêm DB writes và complexity crash reconciliation. Crash trước/sau send có outcome unknown; không thể hứa exactly-once external inference.
- Rule-based ranking cần bộ tiêu chí được product duyệt và dữ liệu đánh giá; không mặc định có chất lượng stylist cao chỉ vì deterministic.
- Bounded generation có thể bỏ lỡ phối đồ ngoài pool; cần đo coverage/diversity trước tăng caps. Không bịa benchmark để biện minh.
- Legacy compatibility cần reader/version lifecycle; rollback binary thuần túy có thể không an toàn với JSON v2.

## Guardrails và điều kiện chấp nhận

1. A trước; gate nghiệm thu A có evidence riêng rồi mới mở B. P1 đi qua verification/configuration gate riêng, không giả lập capability.
2. Không fallback provider ngầm. Escalation là route được phê duyệt có reason/bound/snapshot, không chữa lỗi config bằng provider khác.
3. Audit/budget theo physical request, giữ usage unknown, không giả cost=0; không đưa giá/latency chưa đo vào kết luận.
4. Không model-driven persistence hoặc arbitrary tool execution; ownership/active/consent/schema/locks nằm ở server deterministic.
5. Acceptance và migration/concurrency trên test resources theo [06](06-vlm-test-strategy.md). Unit mocks không chứng minh provider thật hoặc PostgreSQL migration.

## Rollback và điều kiện xem xét lại ADR

- Tắt enqueue/writer/stage/B theo flag; giữ schema additive, drafts/garments/events/audit, không reset budgets để retry. Dùng compatibility release, pin version-compatible worker và dừng job version chưa hỗ trợ.
- Không drop audit/cột đã chứa dữ liệu thật hoặc xóa garment để rollback code. Forward-fix có scope/evidence và phê duyệt; request external đã gửi không thể hoàn tác bằng rollback ứng dụng.
- Chỉ xem xét agent framework ở ADR mới khi có use case branching/tool planning vượt deterministic policy, benchmark/reliability evidence, quyền tool hẹp, cost bounds, replay/debug và migration plan rõ. Framework không được thay thế hard ownership/validity gates.
- Chỉ thay ranker bằng learned model sau dataset/consent/evaluation/abuse controls, vẫn giữ hard constraints và baseline deterministic để so sánh/rollback.

## Tài liệu liên quan

- [01 — AS-IS](01-vlm-as-is-investigation.md)
- [02 — Gap analysis](02-vlm-gap-analysis.md)
- [03 — TO-BE, routing và cost formulas](03-vlm-to-be-architecture.md)
- [04 — Data/API/DB và compatibility FE đề xuất](04-vlm-data-contracts.md)
- [05 — Lộ trình, risks và rollback](05-vlm-implementation-plan.md)
- [06 — Acceptance và test strategy](06-vlm-test-strategy.md)
