#!/usr/bin/env bash
set +x
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT/common.sh"
# Down chỉ dùng project/service labels, không cần đọc env/key runtime kể cả khi
# file đó bị xóa hoặc sửa lỗi cú pháp. Không kết nối DB bằng secret của sample.
export GARBLO_ENV_FILE="$ROOT/.env.sample"
prepare_compose
"${COMPOSE[@]}" down
printf 'Đã dừng project %s; giữ nguyên named volumes, DB và ảnh.\n' "$GARBLO_COMPOSE_PROJECT"
