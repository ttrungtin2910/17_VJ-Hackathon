#!/usr/bin/env bash
set +x
set -Eeuo pipefail
[[ "${GARBLO_LIVE_TEST:-}" == 1 ]] || { printf 'NOT TESTED: cần opt-in GARBLO_LIVE_TEST=1, ảnh có phép và budget được duyệt.\n' >&2; exit 1; }
for name in GARBLO_LIVE_IMAGE_PATH GARBLO_LIVE_MAX_REQUESTS GARBLO_LIVE_MAX_COST_USD GARBLO_LIVE_EMAIL GARBLO_LIVE_PASSWORD; do
  [[ -n "${!name:-}" ]] || { printf 'BLOCKED: thiếu %s; chưa gọi provider.\n' "$name" >&2; exit 1; }
done
[[ "${GARBLO_LIVE_IMAGE_LICENSED:-}" == 1 ]] || { printf 'BLOCKED: cần xác nhận GARBLO_LIVE_IMAGE_LICENSED=1.\n' >&2; exit 1; }
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$ROOT/common.sh"
[[ "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl && "$GARBLO_TEST_MODE" == 0 && -z "$OVERRIDE" ]] || die 'Live entrypoint không chấp nhận test/controlled profile.'
[[ -f "$GARBLO_ENV_FILE" ]] || die 'Cần env runtime và stack đã start.'
prepare_compose
python3 "$ROOT/tests/live_smoke.py" "${COMPOSE[@]}"
