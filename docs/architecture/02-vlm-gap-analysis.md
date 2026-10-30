# 02 — Gap analysis VLM và recommendation

## Tóm tắt và ranh giới

AS-IS được đóng băng tại commit `52346ec95f53bb29b28a8f48c62efbf8097ce2ab`, ngày 30/10/2026, **trước thay đổi A/B**. `IMPLEMENTED` bên dưới là kết luận đọc source, không phải nghiệm thu. TO-BE là **DESIGN**, kể cả khi main agent đang thực thi song song. Không cập nhật ngược hiện trạng lịch sử bằng kết quả triển khai mới.

- **A / P0:** multi-component metadata v2, details/uncertainty strict, review reject/restore, partial/idempotent commit an toàn, audit/budget từng physical call và version snapshots.
- **B / gate:** chỉ triển khai sau nghiệm thu A; recommendation trên own active garments, bounded generation/ranking Top 3, typed edits và locked slots, feedback idempotent và personalization bounded, độc lập image generation.
- **P1:** screening, segmentation thật, escalation và dedup cross-asset có configuration/verification gate. Không giả lập chúng bằng lời mô tả VLM.

## Gap matrix A

| Năng lực | AS-IS và bằng chứng baseline | Khoảng trống | Thiết kế/ưu tiên | Nghiệm thu cần có |
|---|---|---|---|---|
| Decode/giới hạn ảnh/EXIF | `IMPLEMENTED`; `src/garblo/images.py:9-43`, `src/garblo/api/routes.py:180-219` | Không phải screening ngữ nghĩa; DB lỗi sau object write có thể orphan | Tái sử dụng P0; cleanup có kiểm soát | A01, A12 |
| Screening nội dung riêng | `NOT_FOUND`; worker đi thẳng `_analyze` tại `src/garblo/worker.py:180-186`, `246-264` | Chưa phân loại unsuitable qua stage độc lập | P1 disabled mặc định; gate provider/capability/safety | P101 |
| Routing provider/model | `IMPLEMENTED` chọn OpenAI/FPT rõ và legacy Azure; `src/garblo/schemas.py:85-86`, `src/garblo/ai_providers/registry.py:32-45` | Chưa routing nhiều stage hoặc verification registry | P0 giữ selection/snapshot; P1 stage policy explicit | A09, P101 |
| Structured JSON | `PARTIAL`; strict local parser `src/garblo/ai_providers/contracts.py:15-28`, `69-76` | Không có v2/details; native structured output chưa được cấu hình tại `src/garblo/ai_providers/openai_direct.py:43-54` | Versioned schema, local validation luôn bắt buộc; native mode chỉ dùng nếu xác minh | A02–A04 |
| Multi-component | `NOT_FOUND`; `max_length=1` tại `src/garblo/ai_providers/contracts.py:27-28` | Bỏ sót đồ phụ/ảnh outfit, không có bound phù hợp | A: nhiều candidate metadata, trần cấu hình/cứng; không đổi nghĩa thành segmentation | A02, A05 |
| Details/uncertainty | `PARTIAL`; prompt dùng null, không bịa confidence tại `src/garblo/ai_providers/contracts.py:37-43` | Thiếu subcategory/màu phụ/pattern/style, uncertain fields và review reason typed | A: details object strict, enums/allowlist/độ dài, null=không biết; confidence không calibration không được coi xác suất | A03, A04 |
| Human review | `IMPLEMENTED` draft/edit tại `src/garblo/api/routes.py:289-318` | Chưa reject/restore; sửa đồng thời có thể ghi đè | A: state machine, decision rõ, revision nếu áp dụng | A05, A06 |
| Persist sau duyệt | `PARTIAL`; không auto garment; commit tại `src/garblo/api/routes.py:321-357` | Asset luôn catalogued; expansion attributes vào ORM không còn an toàn khi thêm details | A: allowlisted mapping, whole-batch validation, aggregate state từ mọi candidate | A07, A08 |
| Commit idempotency | `IMPLEMENTED` cùng ID đã committed dùng lại garment; lock tại `src/garblo/api/routes.py:323-353`; unique `src/garblo/models.py:168-186` | Chưa thử multi-item partial/reject/concurrent overlap | A: lock order ổn định, không nhân garment, reject batch sai trước mọi write | A07, A08; PostgreSQL |
| Retry/recovery | `IMPLEMENTED`; `src/garblo/worker.py:88-162`, `214-237`; retry route `src/garblo/api/routes.py:526-567` | Attempt claim không chặn được tổng physical calls/repair | A: persistent call reservation + budgets xuyên retry/reclaim | A09–A11 |
| Consent queued/running | `IMPLEMENTED`; `src/garblo/worker.py:154-162`, `191-197` | Request đang bay không thu hồi được; audit mới phải không mất khi output bị bỏ | A: check trước mọi physical call, trước persist; lưu dấu call đã phát sinh không chứa ảnh | A10, A12 |
| Dedup/re-analysis | `PARTIAL`; checksum indexed `src/garblo/models.py:76`, chặn cùng asset `src/garblo/api/routes.py:272-276` | Chưa dedup hai asset; retry có thể đã tốn provider call | A giữ idempotency cùng job; P1 dedup scoped owner+bytes+versions | A08–A11, P104 |
| Audit và usage | `PARTIAL`; output chỉ usage cuối tại `src/garblo/worker.py:288-314`; repair tại `src/garblo/ai_providers/openai_direct.py:78-97` | Mất failed/repair call, không ledger/budget | A: row mỗi physical request, reservation trước IO, usage nullable, unknown không bằng 0 | A10, A11 |
| Version provenance | `PARTIAL`; provider/model/requested_at tại `src/garblo/jobs.py:30-39`, original attrs `src/garblo/models.py:181-182` | Thiếu schema/prompt/pipeline/policy version, không replay được đúng contract | A: immutable snapshots; legacy parse path riêng, không tự đổi phiên bản job cũ | A09 |
| Segmentation thật | `NOT_FOUND`; không derived/mask relation trong `src/garblo/models.py:68-99`, candidate `168-186` | Nhiều metadata vẫn cùng source image | P1 segmentation adapter + crop/mask lineage, quality gate | P102 |
| Escalation | `NOT_FOUND`; resolve một provider/model tại `src/garblo/worker.py:181-186` | Không stage gọi nâng cấp có lý do/bound | P1 explicit route, reason allowlist, ≤ bound snapshot; vẫn human review | P103 |
| Xóa và retention | `PARTIAL`; storage delete `src/garblo/storage.py:71-72`; không route purge trong router baseline | Chưa lifecycle gốc→derived→audit | Phê duyệt retention/purge; không lưu raw output mặc định | A12, P105 |

Mã test/acceptance ở [06](06-vlm-test-strategy.md); đây là test **cần thực hiện**, không phải kết quả.

## Gap matrix B — sau nghiệm thu A

| Năng lực | AS-IS và bằng chứng baseline | TO-BE B (`DESIGN`) | Điều kiện chấp nhận |
|---|---|---|---|
| Context understanding | `NOT_FOUND` pipeline riêng; lookbook nhận preset ở `src/garblo/api/routes.py:408-415` | Normalize context typed; thiếu thời tiết/vị trí dùng unknown hoặc hỏi lại; không tự gọi dịch vụ ngoài | B01 |
| Own active wardrobe | Có owner check, chưa active filter ở `src/garblo/api/routes.py:416-423`, `src/garblo/worker.py:322-339` | Query `user_id=current_user AND catalog_status=active`; revalidate khi edit/save | B01, B04 |
| Outfit generation | `NOT_FOUND` bộ sinh phối đồ; garment IDs do client chọn tại `src/garblo/schemas.py:130-138` | Slot templates + hard constraints + caps số món/slot và tổng tổ hợp | B02 |
| Ranking Top 3 | `NOT_FOUND`; tên `cmax_score` ở `src/garblo/models.py:131` không phải scoring implementation | Rule weights versioned, stable tie-break, diversity; trả tối đa 3, không bịa món nếu thiếu | B02, B03 |
| Chỉnh hội thoại | `PARTIAL` route variation trả 422 tại `src/garblo/api/routes.py:496-515` | Typed edit command; NLP optional chuyển thành command rồi validate; locked slots không đổi | B04 |
| Feedback | Ghi like/dislike append-only tại `src/garblo/api/routes.py:608-624` | Idempotency key + payload hash + unique owner/request scope; feedback không approval | B05 |
| Personalization | `NOT_FOUND` reader/ranker; `style_profile` chỉ là cột tại `src/garblo/models.py:51` | Bounded feature-weight deltas, clip/decay, version và reset/opt-out; không học ownership/safety rules | B06 |
| Độc lập imagegen | Lookbook phụ thuộc image model, `src/garblo/jobs.py:22-23`, `src/garblo/ai_providers/openai_direct.py:100-152` | Recommendation JSON không cần image key/worker render; imagegen là action riêng | B07 |

## Những gì tái sử dụng và không tái sử dụng nguyên trạng

- Giữ JWT, owner-scoped DB access, consent checks, image sanitizer, storage abstraction, queue/lease/fencing/backoff, candidate staging, transaction boundaries và provider registry.
- Không thay toàn bộ schema phẳng: giữ category/color/material/season/tags để client cũ đọc; details thêm mới có version và mapping rõ.
- Không dùng `**candidate.attributes_json` đưa mọi trường model vào ORM. Không coi `source_asset_id` là crop; không tự điền crop ID hoặc mask rỗng để giả lập segmentation.
- Không dùng planner image prompt làm recommendation ranking. Không coi click approval là like; không chuyển feedback thành câu lệnh cho model.
- Không đưa ID model trong tài liệu nguồn vào default deployment: capability/quyền truy cập chưa được kiểm tra trong lượt này.

## Gap → quyết định → kiểm chứng

| Nhóm quyết định | Lý do | Tài liệu thiết kế | Phải được Tech Lead chốt |
|---|---|---|---|
| A contract/state/persistence | Multi metadata làm lộ bug aggregate state và mapping | [04](04-vlm-data-contracts.md), [05](05-vlm-implementation-plan.md) | Trần candidates, enums, semantics unknown/null, reject/restore, mixed batch |
| Per-call audit/budget | Claim retry không bao phủ repair và timeout mơ hồ | [03](03-vlm-to-be-architecture.md), [04](04-vlm-data-contracts.md) | Call/token/money caps, retention, reservation unknown, API visibility |
| B deterministic service | Thỏa wardrobe thật, testable, không buộc imagegen | [ADR](adr-001-deterministic-pipelines.md) | Templates/score/diversity, locked semantics, feedback window và opt-out |
| P1 providers | Tránh capability giả và implicit fallback | [03](03-vlm-to-be-architecture.md) | Capability evidence, consent scope, authorized spend, rollback flags |

Không có gap nào được đánh dấu đã đóng bằng việc viết tài liệu. Báo cáo nghiệm thu riêng cần ghi revision triển khai, lệnh/exit code, môi trường, ca không chạy và bằng chứng thực tế.
