#!/usr/bin/env bash
set +x
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT/common.sh"
[[ -f "$GARBLO_ENV_FILE" ]] || die "Chưa có env. Copy $ROOT/.env.sample thành $ROOT/.env và nhập OpenAI key."
prepare_compose
trap 'printf "Khởi động lỗi; giữ nguyên dữ liệu.\n" >&2; logs_hint >&2' ERR
# Resolved config chỉ được xử lý trong memory, không in secrets hoặc chạy dotenv.
python3 "$ROOT/runtime-check.py" validate "${COMPOSE[@]}"
"${COMPOSE[@]}" build api web
"${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 120 postgres rustfs
"${COMPOSE[@]}" up -d --no-build --force-recreate migrate storage-init
python3 "$ROOT/runtime-check.py" wait-init "${COMPOSE[@]}"
# Init đã được kiểm exit code; không cho Compose tự chạy migration lần hai.
"${COMPOSE[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 api worker web
printf 'Stack %s đã qua readiness DB/schema/storage/API/worker/web; chưa chứng minh AI live.\n' "$GARBLO_COMPOSE_PROJECT"
logs_hint
