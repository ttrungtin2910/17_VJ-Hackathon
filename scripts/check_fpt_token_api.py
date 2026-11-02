"""Make a safe, one-off smoke-test request to the FPT Token API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from openai import APIStatusError, OpenAI


DEFAULT_BASE_URL = "https://token-api.fpt.ai"
DEFAULT_MODEL = "Azure:gpt-5.6-terra"
DEFAULT_PROMPT = "Can you tell me about the creation of blackholes?"
DEFAULT_MAX_TOKENS = 10_000


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def redact(value: str, secret: str) -> str:
    return value.replace(secret, "[REDACTED]") if secret else value


def public_text(payload: object) -> str:
    """FPT may return private thinking blocks; this smoke test intentionally ignores them."""
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise ValueError("FPT response does not contain a content array")
    value = "".join(
        block.get("text", "")
        for block in payload["content"]
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()
    if not value:
        raise ValueError("FPT response does not contain a public text block")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=os.getenv("FPT_TEST_PROMPT", DEFAULT_PROMPT))
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.getenv("FPT_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        help="Maximum generated tokens (default: {}).".format(DEFAULT_MAX_TOKENS),
    )
    return parser.parse_args()


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    load_dotenv(project_root / ".env")
    args = parse_args()

    api_key = os.getenv("FPT_API_KEY", "").strip()
    if not api_key:
        print("FPT_API_KEY is missing. Set it in .env or the environment; no API request was made.", file=sys.stderr)
        return 2
    if args.max_tokens < 1:
        print("--max-tokens must be greater than zero.", file=sys.stderr)
        return 2

    base_url = os.getenv("FPT_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
    model = os.getenv("FPT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    client = OpenAI(api_key=api_key, base_url=base_url)

    try:
        response = client.post(
            "/v1/messages",
            cast_to=httpx.Response,
            body={
                "model": model,
                "max_tokens": args.max_tokens,
                "messages": [{"role": "user", "content": args.prompt}],
                "stream": True,
            },
        )
        response.raise_for_status()
    except (httpx.HTTPStatusError, APIStatusError) as exc:
        detail = redact(exc.response.text[:2_000], api_key)
        print("FPT API returned HTTP {}.".format(exc.response.status_code), file=sys.stderr)
        if detail:
            print(detail, file=sys.stderr)
        return 1
    except Exception as exc:
        print("FPT API request failed: {}".format(redact(str(exc), api_key)), file=sys.stderr)
        return 1

    try:
        print("FPT API returned HTTP {}.".format(response.status_code))
        print(redact(public_text(json.loads(response.text)), api_key))
    except (json.JSONDecodeError, ValueError) as exc:
        print("FPT API returned an invalid response: {}".format(redact(str(exc), api_key)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
