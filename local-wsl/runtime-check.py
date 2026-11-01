"""Đọc resolved config trong memory, không thực thi dotenv và không in secret."""

import json
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit


def docker(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError("Docker/config không khả dụng; kiểm tra daemon và cú pháp env")
    return result.stdout


def validate(command):
    config = json.loads(docker([*command, "config", "--format", "json"]))
    project = os.environ["GARBLO_COMPOSE_PROJECT"]
    if config.get("name") != project:
        raise ValueError("Resolved project không khớp target")
    services = config["services"]
    if set(services) != {"postgres", "rustfs", "storage-init", "migrate", "api", "worker", "web"}:
        raise ValueError("Danh sách services không khớp local stack")
    for volume in config.get("volumes", {}).values():
        if volume.get("external") or not volume.get("name", "").startswith(project + "_"):
            raise ValueError("Volume phải riêng theo project; không dùng external/runtime volume")
    public = services["web"].get("build", {}).get("args", {})
    if set(public) != {"VITE_DATA_MODE", "VITE_API_BASE_URL"} or public["VITE_DATA_MODE"] != "real":
        raise ValueError("FE build chỉ được nhận allowlist public ở chế độ real")
    if services["web"].get("environment") or services["web"].get("volumes"):
        raise ValueError("Không mount env/source hoặc inject secret vào FE")
    environment = services["api"].get("environment", {})
    for name in ("api", "worker", "migrate", "storage-init"):
        values = services[name].get("environment", {})
        database = urlsplit(values.get("DATABASE_URL", ""))
        storage = urlsplit(values.get("S3_ENDPOINT_URL", ""))
        if database.hostname != "postgres" or storage.hostname != "rustfs":
            raise ValueError("Runtime local chỉ được dùng DB/storage nội bộ của project")
        if project == "garblo-local-wsl-test" and (not database.path.endswith("_test") or not values.get("S3_BUCKET", "").startswith("garblo-test-")):
            raise ValueError("Test cần DB _test và bucket riêng, không target runtime")
    for network in config.get("networks", {}).values():
        if network.get("external"):
            raise ValueError("Không kết nối network ngoài project local")
    if os.environ.get("GARBLO_TEST_MODE") == "1":
        for service in services.values():
            env = service.get("environment", {})
            if any(env.get(key) for key in ("OPENAI_API_KEY", "FPT_VLM_API_KEY", "AZURE_OPENAI_API_KEY")):
                raise ValueError("Test mode không được có AI key")
    elif not environment.get("OPENAI_API_KEY", "").strip():
        raise ValueError("Cần nhập OPENAI_API_KEY trong .env trước khi start")
    elif not environment.get("FPT_VLM_API_KEY"):
        print("FPT key trống: dùng OpenAI-only trong budget hiện có.")
    # Port đã thuộc đúng project không phải conflict; không dừng process/stack khác.
    ids = docker(["docker", "ps", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}} "]).split()
    owned = set()
    if ids:
        for container in json.loads(docker(["docker", "inspect", *ids])):
            for bindings in container.get("NetworkSettings", {}).get("Ports", {}).values():
                for binding in bindings or []:
                    owned.add((binding["HostIp"], int(binding["HostPort"])))
    for service in services.values():
        for binding in service.get("ports", []):
            host, port = binding.get("host_ip"), int(binding["published"])
            if host != "127.0.0.1":
                raise ValueError("Cổng local phải bind 127.0.0.1")
            if (host, port) not in owned:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.bind((host, port))
                    except OSError:
                        raise ValueError(f"Cổng {port} đang do process khác dùng; không tự dừng") from None
    for name in ("web", "api"):
        port = services[name]["ports"][0]["published"]
        print(f"{name}: http://localhost:{port}" + ("/docs" if name == "api" else ""))


def wait_init(command):
    deadline = time.monotonic() + 180
    for service in ("migrate", "storage-init"):
        while time.monotonic() < deadline:
            ids = docker([*command, "ps", "--all", "-q", service]).split()
            if ids:
                state = json.loads(docker(["docker", "inspect", ids[0]]))[0]["State"]
                if state["Status"] == "exited":
                    if state["ExitCode"] != 0:
                        raise ValueError(f"{service} lỗi; giữ nguyên DB/volume để kiểm tra")
                    break
                if state["Status"] == "dead":
                    raise ValueError(f"{service} đã chết")
            time.sleep(0.5)
        else:
            raise ValueError(f"Timeout chờ {service}")


if __name__ == "__main__":
    try:
        action, *command = sys.argv[1:]
        {"validate": validate, "wait-init": wait_init}[action](command)
    except Exception as error:
        # Chỉ các thông báo tự định nghĩa được xuất; JSON/Docker raw errors bị ẩn.
        message = str(error) if isinstance(error, ValueError) and type(error) is ValueError else "Không kiểm tra được runtime/config"
        print(message, file=sys.stderr)
        raise SystemExit(1) from None
