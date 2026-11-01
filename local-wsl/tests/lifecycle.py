"""Lifecycle Docker thật chỉ cho project test; không gọi provider hoặc xóa volume."""

import base64
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "garblo-local-wsl-test"
if os.environ.get("GARBLO_COMPOSE_PROJECT", PROJECT) != PROJECT:
    raise SystemExit("Từ chối lifecycle ngoài project test")
ENV = {
    **os.environ,
    "GARBLO_COMPOSE_PROJECT": PROJECT,
    "GARBLO_ENV_FILE": str(ROOT / "tests/env.test"),
    "GARBLO_COMPOSE_OVERRIDE_FILE": str(ROOT / "deploy/compose.test.yml"),
    "GARBLO_TEST_MODE": "1",
}
COMPOSE = ["docker", "compose", "--project-name", PROJECT, "--project-directory", str(ROOT), "--env-file", ENV["GARBLO_ENV_FILE"], "-f", str(ROOT / "deploy/compose.local.yml"), "-f", ENV["GARBLO_COMPOSE_OVERRIDE_FILE"]]


def api(path, token=None, payload=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
    request = urllib.request.Request("http://localhost:18081" + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def snapshot():
    # Chỉ xuất digest tổng hợp, không in IDs hoặc metadata từng user.
    query = "SELECT md5(string_agg(payload, '|' ORDER BY name)) FROM (" + " UNION ALL ".join(
        f"SELECT '{table}' AS name, coalesce(jsonb_agg(to_jsonb(t) ORDER BY id)::text, '[]') AS payload FROM {table} t"
        for table in ("media_assets", "garments", "recommendation_sessions", "recommendation_feedback")
    ) + ") snapshot"
    return subprocess.check_output([*COMPOSE, "exec", "-T", "postgres", "psql", "-U", "garblo", "-d", "garblo_test", "-At", "-c", query], env=ENV, text=True).strip()


def run():
    with tempfile.TemporaryDirectory(prefix="garblo lifecycle cwd ", dir="/tmp/opencode") as cwd:
        def script(name):
            result = subprocess.run([str(ROOT / name)], cwd=cwd, env=ENV, capture_output=True, timeout=360)
            if result.returncode:
                raise RuntimeError(f"{name} exit {result.returncode}; không in raw log/env")

        script("start-all.sh")
        script("start-all.sh")
        email = f"lifecycle-{uuid.uuid4().hex}@example.com"
        password = "test-lifecycle-password-only"
        api("/v1/auth/register", payload={"email": email, "display_name": "Lifecycle test", "password": password})
        token = api("/v1/auth/login", payload={"email": email, "password": password})["access_token"]
        # PNG tổng hợp 1×1; không ảnh cá nhân và không enqueue inference.
        image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        boundary = uuid.uuid4().hex
        body = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + image + f"\r\n--{boundary}--\r\n".encode()
        asset = api("/v1/assets", token, body, f"multipart/form-data; boundary={boundary}")

        def bytes_hash():
            signed = api(f"/v1/assets/{asset['id']}/download", token)["url"]
            # Không gửi JWT sang storage, không in signed URL.
            with urllib.request.urlopen(signed, timeout=20) as response:
                return hashlib.sha256(response.read()).hexdigest()

        before = snapshot(), bytes_hash()
        script("stop-all.sh")
        script("stop-all.sh")
        script("start-all.sh")
        if api(f"/v1/assets/{asset['id']}", token) != asset or (snapshot(), bytes_hash()) != before:
            raise RuntimeError("Persistence DB/ảnh không khớp sau restart")
        with open(Path(cwd) / "concurrent.log", "wb") as log:
            processes = [subprocess.Popen([str(ROOT / "start-all.sh")], cwd=cwd, env=ENV, stdout=log, stderr=log) for _ in range(2)]
            if any(process.wait(timeout=360) != 0 for process in processes):
                raise RuntimeError("Start đồng thời lỗi")
        if (snapshot(), bytes_hash()) != before:
            raise RuntimeError("Dữ liệu thay đổi ngoài dự kiến khi start đồng thời")
        for service in ("api", "worker", "web"):
            ids = subprocess.check_output([*COMPOSE, "ps", "-q", service], env=ENV, text=True).split()
            if len(ids) != 1:
                raise RuntimeError(f"Service {service} bị thiếu hoặc nhân đôi")
        script("stop-all.sh")
        print("PASS: start×2 → stop×2 → start → concurrent start×2; DB/asset ID/checksum/bytes giữ nguyên; đã stop test project, volumes còn nguyên. Không AI live.")


if __name__ == "__main__":
    run()
