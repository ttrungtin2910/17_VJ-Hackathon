"""Smoke live opt-in: chặn trước inference nếu không chứng minh được trần riêng cho job."""

import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run():
    if os.environ.get("GARBLO_LIVE_TEST") != "1" or os.environ.get("GARBLO_LIVE_IMAGE_LICENSED") != "1":
        raise RuntimeError("Thiếu opt-in/quyền ảnh")
    maximum = int(os.environ["GARBLO_LIVE_MAX_REQUESTS"])
    budget = Decimal(os.environ["GARBLO_LIVE_MAX_COST_USD"])
    if maximum < 1 or not budget.is_finite() or budget <= 0:
        raise RuntimeError("Budget không hợp lệ")
    image_path = Path(os.environ["GARBLO_LIVE_IMAGE_PATH"])
    mime = mimetypes.guess_type(image_path.name)[0]
    if not image_path.is_file() or mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise RuntimeError("Cần file JPEG/PNG/WebP được cấp phép")
    image = image_path.read_bytes()
    compose = sys.argv[1:]
    if not compose or compose[compose.index("--project-name") + 1] != "garblo-local-wsl":
        raise RuntimeError("Chỉ chấp nhận project runtime canonical")

    def docker(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Không kiểm được container runtime")
        return result.stdout

    # Chỉ xuất allowlist cấu hình không nhạy cảm từ container đang chạy, không
    # dùng .env mới sửa làm bằng chứng process hiện tại đã nạp cấu hình đó.
    probe = """import json
from garblo.config import get_settings
s=get_settings()
print(json.dumps({k:getattr(s,k) for k in ['openai_vlm_model','catalogue_max_calls','catalogue_max_output_tokens','catalogue_screening_model','catalogue_escalation_model','catalogue_segmentation_url','catalogue_deadline_seconds','max_upload_bytes']} | {'has_openai_key':bool(s.openai_api_key),'has_fpt_fallback':bool(s.fpt_vlm_api_key and s.catalogue_fpt_fallback_enabled)}))
"""
    profiles = []
    containers = {}
    for service in ("api", "worker"):
        ids = docker([*compose, "ps", "-q", service]).split()
        if len(ids) != 1:
            raise RuntimeError("API/worker chưa chạy duy nhất")
        container = json.loads(docker(["docker", "inspect", ids[0]]))[0]
        containers[service] = container
        command = container["Config"]["Cmd"]
        if (service == "worker" and command != ["garblo-worker"]) or (service == "api" and (command[:2] != ["uvicorn", "garblo.main:app"])):
            raise RuntimeError("Không chấp nhận harness/command chưa xác minh")
        profiles.append(json.loads(docker([*compose, "exec", "-T", service, "python", "-c", probe])))
    profile = profiles[0]
    if profiles[0] != profiles[1] or not profile["has_openai_key"]:
        raise RuntimeError("API/worker thiếu key hoặc khác profile")
    if profile["openai_vlm_model"] != "gpt-5.4-mini-2026-03-17" or profile["has_fpt_fallback"]:
        raise RuntimeError("Chỉ đã chốt giá profile OpenAI mặc định; tắt fallback cho smoke live này")
    if any(profile[key] for key in ("catalogue_screening_model", "catalogue_escalation_model", "catalogue_segmentation_url")):
        raise RuntimeError("Smoke không chấp nhận stages/model chưa chốt giá")
    if len(image) > profile["max_upload_bytes"] or not image:
        raise RuntimeError("File vượt giới hạn upload")
    calls = profile["catalogue_max_calls"]
    # Giá tài liệu model: $0.75 input/$4.5 output mỗi triệu tokens; dùng trần
    # input của model 272000 và max completion đã cấu hình, không đoán usage ảnh.
    bound = Decimal(calls) * (Decimal(272000) * Decimal("0.75") + Decimal(profile["catalogue_max_output_tokens"]) * Decimal("4.5")) / Decimal(1000000)
    if calls > maximum or bound > budget:
        raise RuntimeError("Cap job vượt budget đã duyệt; operator phải hạ cap rõ ràng và start lại, không tự điền budget")
    binding = containers["api"]["NetworkSettings"]["Ports"]["8000/tcp"][0]
    if binding["HostIp"] != "127.0.0.1":
        raise RuntimeError("API không bind loopback")
    base = "http://localhost:" + binding["HostPort"]

    def request(path, token=None, body=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = "Bearer " + token
        if isinstance(body, dict):
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=90) as response:
            return json.load(response)

    token = request("/v1/auth/login", body={"email": os.environ["GARBLO_LIVE_EMAIL"], "password": os.environ["GARBLO_LIVE_PASSWORD"]})["access_token"]
    user = request("/v1/me", token)["id"]
    fingerprint = hashlib.sha256(image).hexdigest()
    state_path = ROOT / ".runtime" / ("live-" + hashlib.sha256((base + user + fingerprint).encode()).hexdigest() + ".json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {"key": str(uuid.uuid4()), "profile": profile}
    if state["profile"] != profile:
        raise RuntimeError("Profile operation cũ đã thay đổi; cần đối soát thủ công")

    def save():
        temporary = state_path.with_suffix(".next")
        temporary.write_text(json.dumps(state))
        temporary.replace(state_path)

    save()  # Không token, ảnh, password hoặc signed URL trong durable state.
    consent = request("/v1/me/consents/latest?purpose=ai_processing", token)
    if not consent or consent["revoked_at"]:
        request("/v1/consents", token, {"purpose": "ai_processing", "policy_version": "local-wsl-1"})
    if "asset" not in state:
        boundary = uuid.uuid4().hex
        body = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="live-image"\r\nContent-Type: {mime}\r\n\r\n'.encode() + image + f"\r\n--{boundary}--\r\n".encode()
        asset = request("/v1/assets", token, body, {"Content-Type": "multipart/form-data; boundary=" + boundary, "Idempotency-Key": state["key"]})
        state["asset"] = asset["id"]
        save()
    path = "/v1/assets/" + state["asset"]
    jobs = request(path + "/jobs", token)
    if not jobs:
        try:
            request(path + "/catalogize", token, {})
        finally:
            jobs = request(path + "/jobs", token)
    if len(jobs) != 1:
        raise RuntimeError("Không xác định được job đúng nguồn")
    state["job"] = jobs[0]["id"]
    save()
    deadline = time.monotonic() + profile["catalogue_deadline_seconds"] + 120
    while time.monotonic() < deadline:
        job = request("/v1/jobs/" + state["job"], token)
        if job["status"] in {"failed", "succeeded"}:
            break
        time.sleep(2)
    else:
        raise RuntimeError("Hết thời gian theo dõi; không retry inference tự động")
    if job["status"] != "succeeded":
        raise RuntimeError("Job không thành công; xem mã lỗi đã redacted trong API")
    audit = request("/v1/jobs/" + state["job"] + "/calls", token)
    if not audit or len(audit) > maximum or any(call["provider"] != "openai" or call["model"] != profile["openai_vlm_model"] for call in audit):
        raise RuntimeError("Audit call/provider/model không khớp budget")
    print(f"PASS phân tích OpenAI live: model={profile['openai_vlm_model']}, job={state['job'][:8]}…, calls={len(audit)}, trần bảo thủ USD={bound}. Đây không phải hard billing cap upstream.")
    print(f"Chưa auto commit: mở FE /capture/{state['asset']}/review, người dùng kiểm tra/chọn/sửa rồi lưu. Recommendation/feedback/persistence live cần nghiệm thu riêng.")


if __name__ == "__main__":
    try:
        run()
    except RuntimeError as error:
        print("BLOCKED/FAIL: " + str(error) + "; không tự retry inference.", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("BLOCKED/FAIL: chưa hoàn tất smoke live. Kiểm tra opt-in, quyền ảnh, account, container/profile và budget; không in raw exception/token/URL ký.", file=sys.stderr)
        raise SystemExit(1) from None
