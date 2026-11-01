# Local Schema Version Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cho phép cấu hình schema catalogue từ biến môi trường mà không làm migrate/storage-init dừng trước khi kết nối dịch vụ.

**Architecture:** Giữ `Literal[1, 2]` và thêm validator chạy trước validation để chuyển riêng chuỗi `"1"`/`"2"` thành số. Không bỏ biến môi trường để che lỗi, không sửa migration hoặc reset dữ liệu.

**Tech Stack:** Python 3.12, Pydantic Settings 2, pytest, Docker Compose.

**Spec:** Yêu cầu kiểm tra lỗi `local-wsl/start-all.sh` trong phiên làm việc ngày 2026-11-02: giữ nguyên DB/volume, redaction log trước khi chia sẻ.

## Global Constraints

- Giữ nguyên DB/volume; không `down -v`, reset DB hoặc stamp Alembic.
- Không sửa migration đã phát hành `migrations/versions/20261018_0001_initial_schema.py`.
- Không ghi/in secret từ `.env`, Docker inspect hoặc resolved config.
- Giữ nguyên các thay đổi có sẵn của người dùng; không tự commit.
- Chạy kiểm tra trong `garblo-backend/`; chưa chạy lại migration trên DB hiện hữu nếu chưa được đồng ý.

## Review Focus

- Env `"1"` và `"2"` phải cho ra giá trị kiểu `int` tương ứng.
- Dotenv cũng phải có cùng hành vi như environment.
- Giá trị số truyền trực tiếp `1`, `2` phải tiếp tục hợp lệ.
- Khi không cấu hình, mặc định vẫn là số `2`.
- Chuỗi không thuộc allowlist (`"0"`, `"3"`, `"abc"`, `"2.0"`, rỗng) vẫn bị từ chối.

---

### Task 1: Chuẩn hóa schema version và thêm test hồi quy

**Files:**
- Modify: `src/garblo/config.py`
- Test: `tests/test_pipeline_settings.py`

**Interfaces:**
- Consumes: `Settings(_env_file=None, **values)`; env `CATALOGUE_SCHEMA_VERSION`.
- Produces: `Settings.catalogue_schema_version` vẫn là `Literal[1, 2]`; validator `parse_catalogue_schema_version(cls, value)` chỉ chuẩn hóa hai chuỗi được phép.

- [ ] **Step 1:** Thêm test parametrized cho env `"1"`/`"2"`, dotenv tạm với hai giá trị này, số trực tiếp `1`/`2`, mặc định `2`, và các chuỗi không hợp lệ trong Review Focus. Dùng `monkeypatch`, `_env_file=None` hoặc dotenv tạm; không đọc `.env` thật. Assert cả giá trị và `type(value) is int` cho trường hợp hợp lệ; assert `ValidationError` cho trường hợp không hợp lệ.
- [ ] **Step 2:** Chạy `python -m pytest -q tests/test_pipeline_settings.py`; xác nhận test env/dotenv thất bại vì `literal_error` trước khi sửa.
- [ ] **Step 3:** Thêm `@field_validator("catalogue_schema_version", mode="before")` trong `Settings`; chỉ chuyển chuỗi `"1"`/`"2"` sang `int`, giữ nguyên các đầu vào khác để Literal tiếp tục kiểm tra.
- [ ] **Step 4:** Chạy `python -m pytest -q tests/test_pipeline_settings.py`, `ruff check src/garblo/config.py tests/test_pipeline_settings.py`, `ruff format --check src/garblo/config.py tests/test_pipeline_settings.py`; tất cả phải exit 0. Chạy suite non-integration trong môi trường test không có AI keys; không coi skip là bằng chứng runtime.
- [ ] **Step 5:** Sau khi được đồng ý, rebuild image rồi chạy lại `local-wsl/start-all.sh` với nguyên volume hiện hữu. Kiểm tra exit code của cả migrate/storage-init và readiness api/worker/web; chỉ xuất log sau redaction. Readiness không chứng minh AI live.
