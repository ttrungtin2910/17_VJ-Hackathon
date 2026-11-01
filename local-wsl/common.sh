#!/usr/bin/env bash
# Chỉ nạp script do dự án quản lý; tuyệt đối không source/eval dotenv.
set +x
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export GARBLO_COMPOSE_PROJECT="${GARBLO_COMPOSE_PROJECT:-garblo-local-wsl}"
export GARBLO_TEST_MODE="${GARBLO_TEST_MODE:-0}"
export GARBLO_ENV_FILE="${GARBLO_ENV_FILE:-$ROOT/.env}"
OVERRIDE="${GARBLO_COMPOSE_OVERRIDE_FILE:-}"

die() { printf '%s\n' "$*" >&2; exit 1; }
[[ "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl || "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl-test ]] || die 'Project không nằm trong allowlist Garblo local.'
[[ "$GARBLO_ENV_FILE" == /* ]] || die 'GARBLO_ENV_FILE phải là absolute path.'
[[ -z "$OVERRIDE" || "$OVERRIDE" == /* ]] || die 'Override phải là absolute path.'
[[ -z "$OVERRIDE" || "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl-test ]] || die 'Override chỉ dành cho project test riêng.'
if [[ "$GARBLO_TEST_MODE" == 1 ]]; then
  [[ "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl-test && -n "$OVERRIDE" && "$GARBLO_ENV_FILE" != "$ROOT/.env" ]] || die 'Test mode chỉ dành cho test project với env/override riêng.'
fi
if [[ "$GARBLO_COMPOSE_PROJECT" == garblo-local-wsl-test ]]; then
  [[ "$GARBLO_ENV_FILE" != "$ROOT/.env" && -n "$OVERRIDE" ]] || die 'Test project cần env/override riêng.'
fi
for tool in docker python3 flock; do command -v "$tool" >/dev/null || die "Thiếu công cụ: $tool"; done
umask 077
mkdir -p -- "$ROOT/.runtime"
exec 9>"$ROOT/.runtime/$GARBLO_COMPOSE_PROJECT.lock"
flock -w 300 9 || die 'Không lấy được lock startup; thử lại sau.'

prepare_compose() {
  COMPOSE=(docker compose --project-name "$GARBLO_COMPOSE_PROJECT" --project-directory "$ROOT" --env-file "$GARBLO_ENV_FILE" -f "$ROOT/deploy/compose.local.yml")
  if [[ -n "$OVERRIDE" ]]; then COMPOSE+=(-f "$OVERRIDE"); fi
  docker compose version >/dev/null 2>&1 || die 'Docker Compose chưa sẵn sàng.'
  docker info >/dev/null 2>&1 || die 'Không truy cập được Docker daemon.'
}

logs_hint() {
  printf 'Xem log (đọc và redaction trước khi chia sẻ):\n'
  printf '%q ' "${COMPOSE[@]}" logs --tail=100 api worker
  printf '\n'
}
