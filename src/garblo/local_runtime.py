"""Readiness local chỉ kiểm DB/schema/private bucket, không gọi provider."""

import json
import os
import sys
import time
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from botocore.config import Config as S3Config
from sqlalchemy import text

from garblo.config import get_settings
from garblo.database import SessionLocal

HEARTBEAT = Path("/tmp/garblo-worker-heartbeat")


def check_runtime(settings, database=SessionLocal, storage_factory=None, expected_head=None):
    checks = {"database": False, "schema": False, "storage": False}
    try:
        if expected_head is None:
            expected_head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        with database() as db:
            checks["database"] = db.scalar(text("SELECT 1")) == 1
            checks["schema"] = (
                db.scalar(text("SELECT version_num FROM alembic_version")) == expected_head
            )
    except Exception:
        pass  # Chỉ trả bool, không phản chiếu DSN/raw exception.
    try:
        if storage_factory:
            client = storage_factory(settings)
        else:
            import boto3

            if not settings.storage_is_configured:
                raise ValueError("Storage chưa được cấu hình")
            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                config=S3Config(
                    connect_timeout=2, read_timeout=2, retries={"total_max_attempts": 1}
                ),
            )
        client.head_bucket(Bucket=settings.s3_bucket)
        close = getattr(client, "close", None)
        if close:
            close()
        checks["storage"] = True
    except Exception:
        pass
    return checks


def write_heartbeat(state, path=HEARTBEAT, now=None):
    temporary = path.with_suffix(".next")
    temporary.write_text(
        json.dumps(
            {"state": state, "time": time.time() if now is None else now, "pid": os.getpid()}
        )
    )
    temporary.replace(path)


def heartbeat_ready(settings, path=HEARTBEAT):
    try:
        value = json.loads(path.read_text())
        os.kill(value["pid"], 0)
        age = time.time() - value["time"]
        limit = settings.worker_lease_seconds + 30 if value["state"] == "busy" else 15
        return value["state"] in {"idle", "busy"} and 0 <= age <= limit
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run():
    settings = get_settings()
    checks = check_runtime(settings)
    ready = all(checks.values())
    if sys.argv[1:] == ["worker"]:
        ready = ready and heartbeat_ready(settings)
    raise SystemExit(0 if ready else 1)


if __name__ == "__main__":
    run()
