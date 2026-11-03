# Stylist Agent Implementation Plan

**Goal:** Tạo và chỉnh outfit bằng tiếng Việt, một planner và tối đa một nghiệp vụ mỗi tin nhắn.

**Architecture:** Endpoint `/stylist/messages` tạo job PostgreSQL. Worker dùng snapshot model/budget, planner structured output rồi gọi recommendation/edit service trong transaction publish có lease fence. Không thêm framework.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, worker hiện có; React và HTTP adapter.

**Spec:** Plan đã được người dùng duyệt trong phiên: tạo + chỉnh outfit, một lượt có tool.

## Global Constraints

- Giao diện và trao đổi tiếng Việt; hai repo riêng; giữ thay đổi hiện có.
- Ownership, consent, revision, idempotency, lease và budget phải được kiểm tra.
- Không inference thật trong kiểm thử tự động.

## Review Focus

- ID ngoại lai không được ghi; constraints/locks không bị model bỏ qua.
- Mất response và retry giữ job ID.
- Clarification là kết quả nghiệp vụ, không giả chỉnh thành công.
- Consent/revision thay đổi trong inference phải chặn publish.
- Tạo phiên và job success phải commit cùng nhau.

## Tasks

- [x] Backend: thêm request/intent, endpoint enqueue và worker dispatch. Tái sử dụng infer/audit; create_session hỗ trợ commit=False.
- [x] Frontend: thêm contract/HTTP job methods, panel tạo/chỉnh, consent rõ ràng, poll và phục hồi job.
- [x] Kiểm thử: mocked HTTP worker cho tạo/chỉnh/hỏi lại, ID giả, consent/revision; adapter replay/poll; typecheck/lint/build. Backend 119 tests; frontend 101 tests; Playwright mobile + desktop 2 tests.
- [x] Tài liệu API và cách cấu hình; đối chiếu diff và báo kết quả thực tế.
