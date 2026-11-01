# Chạy Garblo local trên WSL2

## Điều kiện và giới hạn

- Hai repo `garblo-backend/`, `garblo-frontend/` đặt cạnh nhau trên filesystem Linux của WSL2 Ubuntu; không di chuyển repo hay khởi tạo Git ở workspace root.
- Docker Engine hoặc Docker Desktop bật WSL integration, Docker Compose có `--wait`, Bash, Python 3 và `flock` (util-linux). Không cần Node/Python dependencies trên host để chạy app: build FE dùng Node 24.11.0, API/worker dùng Python 3.12.14 trong container.
- Tại baseline: WSL có RAM 7,6 GiB, khả dụng khoảng 2,5 GiB, disk trống 840 GiB. Đây là số đo của máy kiểm thử, không phải cấu hình tối thiểu được bảo đảm. Cần thêm dung lượng tải/build images, nhất là image Playwright chỉ dùng QA. Không dừng stack khác để lấy tài nguyên.
- Chỉ local HTTP, không production HTTPS/CI/CD/cloud. Secrets mặc định trong sample chỉ dành dev. Không đưa máy này ra Internet với secret mẫu.
- OpenAI vision live cần ảnh được quyền sử dụng và ngân sách được operator duyệt. Khởi động/readiness không gọi provider; runtime/test mock không chứng minh OpenAI/FPT live.

## Ba bước khởi chạy

Từ workspace:

```bash
cd garblo-backend/local-wsl
cp .env.sample .env
# Mở .env, chỉ nhập OPENAI_API_KEY; không thêm key vào VITE_*.
./start-all.sh
```

Không ghi đè `.env` đã có. FPT key tùy chọn; để trống giữ OpenAI-only. Script cảnh báo nhưng không tự đổi provider khi lỗi auth/billing/safety/consent. Không cần image-generation model vì slice dùng composition theo quy tắc, không tạo ảnh AI lookbook/VTON.

Profile mặc định: `gpt-5.4-mini-2026-03-17`, vision + Chat Completions + structured output; không gửi temperature. Đã đối chiếu [tài liệu model chính thức](https://developers.openai.com/api/docs/models/gpt-5.4-mini). Chưa xác minh quyền model của tài khoản hoặc wire-live; không tự thử hàng loạt model khi lỗi. `CATALOGUE_MAX_CALLS`, token/deadline và worker attempts là giới hạn tác vụ, không phải hard billing cap của OpenAI.

Mở:

- FE: <http://localhost:5173>.
- API/docs: <http://localhost:18080/docs>; readiness: <http://localhost:18080/ready>.
- RustFS media endpoint: `http://localhost:19000`; bucket private, không phải trang duyệt ảnh công khai. PostgreSQL không publish host port trong profile thường.

Đăng ký/đăng nhập trên FE, không có account hay tủ mẫu tự tạo. Token chỉ sống trong memory: refresh cần đăng nhập lại đúng tài khoản để tiếp tục. Upload cần checkbox đồng ý; có thể thu hồi tại “Quyền xử lý ảnh”. Thu hồi là checkpoint, không hứa hủy upstream đang chạy/xóa provider data/URL ký cũ. Không gửi ảnh không có quyền sử dụng.

Nhận diện tạo nháp, chưa tạo garment. Sửa/chọn rồi bấm **Lưu vào tủ**. Ảnh/garments/sessions/feedback ở DB/storage local; nháp bối cảnh và references phục hồi chỉ ở thiết bị theo API/user. Không lưu ảnh/token vào metadata real hay demo DB. Mất response upload: chọn lại đúng file, giữ operation; không chọn job “mới nhất” để đoán.

## Dừng, log và restart

```bash
./stop-all.sh
```

Chạy lặp được, không cần key còn hợp lệ và không có `-v`. Named volumes giữ DB/ảnh. Start/stop dùng absolute path được từ cwd khác. Lock theo project serialize startup/migration; không giết process đang giữ port của stack khác.

Từ `garblo-backend/local-wsl/`:

```bash
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml ps --all
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml logs --tail=100 api worker
docker compose --project-name garblo-local-wsl --project-directory "$PWD" --env-file .env -f deploy/compose.local.yml config --quiet
```

Script cũng in lệnh log đã quote bằng absolute paths. Redaction trước khi chia sẻ log. Không dùng plain `config` để in env đã nội suy; helper đọc JSON config trong memory, không xuất secrets. Khi đổi env, chạy lại `start-all.sh` để Compose recreate đúng service; `restart` đơn thuần không nạp env mới.

## Chẩn đoán

| Lỗi | Kiểm tra / cách xử lý |
|---|---|
| Thiếu env/key | Copy sample nếu chưa có, nhập key ở server env; không paste key vào browser/ảnh chụp/log |
| Daemon/permission | `docker info`, `docker compose version`; bật daemon/WSL integration theo chính sách máy; không tự sửa quyền socket toàn hệ thống |
| Port conflict | Kiểm `ss -ltn`; mặc định 5173/18080/19000. Không chiếm 8000/5432 đang thuộc stack khác. Nếu đổi port, cập nhật đồng bộ public API/media URLs và CORS trong env |
| Migration lỗi | Đọc log `migrate`; script exit khác 0, giữ DB/volumes. Không xóa DB để né lỗi; kiểm connection và revision |
| API ready nhưng AI lỗi | `/ready` chỉ kiểm DB/schema/storage. Kiểm key/quyền model/billing/consent bằng thao tác có phép; không tự fallback auth/safety |
| Worker không healthy | Kiểm log/heartbeat; idle quá 15s không ready, busy cho tối đa lease +30s. Heartbeat không có nghĩa request paid thành công |
| Ảnh không hiện | Kiểm public S3 endpoint browser-reachable, private bucket CORS đúng FE origins, URL expiry; FE chỉ renew 403 tối đa một lần, không gửi Bearer sang S3 |
| Windows không mở localhost | Trước tiên `curl -f http://localhost:18080/ready` trong WSL. Kiểm port publish loopback, WSL localhost forwarding/mirrored networking, Docker integration và firewall với quản trị máy; không bảo đảm forwarding trên mọi cấu hình |
| CRLF/quyền chạy | `dos2unix start-all.sh stop-all.sh common.sh`, rồi `chmod +x start-all.sh stop-all.sh`; không dùng PowerShell/native Windows scripts |
| Chưa lưu nháp | Chờ hết pending trước refresh; browser không bảo đảm flush khi đóng tab. Partial PATCH giữ lỗi/chặn commit để đối soát, không báo success giả |
| Real Impact/wear | Chưa có API; UI bị ẩn/guard. Không ghi lượt mặc giả vào local hoặc tăng wear từ like |

## Kiểm thử riêng, không dùng key thật

`local-wsl/tests/env.test` và `deploy/compose.test.yml` dùng project `garblo-local-wsl-test`, cổng 5183/18081/19001 và DB host 25440, DB `garblo_test`. Volumes có project prefix riêng. Không trỏ test vào env/DB/bucket người dùng.

```bash
cd garblo-backend
bash local-wsl/tests/test-scripts.sh
bash local-wsl/tests/test-lifecycle.sh
```

Lifecycle script chỉ target test, dùng chính hai scripts start/stop, tạo ảnh PNG/user QA, giữ test volumes và stop test stack cuối lượt. Không tự reset/xóa test data. Muốn reset, trước tiên liệt kê rõ đúng test project/volumes và xin xác nhận riêng; không cung cấp lệnh xóa mặc định, không dùng `prune` hay `down -v` trên runtime.

Controlled-provider: thay override test bằng `deploy/compose.controlled.yml` (không gộp vào normal startup). Chỉ file dưới `tests/` được mount; harness từ chối DB không `_test`, dùng registry giả thay SDK. Đây là **inference MOCKED**, dù API/DB/storage/worker process là thật. Không dùng kết quả đó để tick AI live.

Browser host thiếu thư viện có thể build image QA mà không cài OS packages lên WSL:

```bash
cd garblo-frontend
docker build -f tests/Dockerfile.e2e -t garblo-local-wsl-test-e2e:local .
docker run --rm --network host garblo-local-wsl-test-e2e:local npm run test:e2e
docker run --rm --network host -e GARBLO_E2E_BASE_URL=http://localhost:5183 garblo-local-wsl-test-e2e:local npx playwright test --config playwright.real.config.ts tests/e2e/real-auth.spec.ts
```

Chỉ thêm `GARBLO_CONTROLLED_E2E=1` cho real capture suite khi test stack đã chạy controlled override; biến này chỉ chọn test, không phải production fake-provider flag. Real tests tắt trace để không ghi JWT/URL ký. Đọc `BAO_CAO_TRIEN_KHAI_LOCAL.md` để biết gate nào thực sự đã chạy; không suy README thành chứng nhận toàn hệ thống.

## Smoke AI live do operator chủ động

`local-wsl/tests/test-live.sh` **không** chạy trong startup hoặc suite thường. Muốn chạy, operator phải chủ động đặt:

- `GARBLO_LIVE_TEST=1`, `GARBLO_LIVE_IMAGE_LICENSED=1` và `GARBLO_LIVE_IMAGE_PATH` là ảnh được cấp phép.
- `GARBLO_LIVE_MAX_REQUESTS`, `GARBLO_LIVE_MAX_COST_USD` là số do operator duyệt; không có budget mặc định.
- `GARBLO_LIVE_EMAIL`, `GARBLO_LIVE_PASSWORD` của tài khoản đã tạo trên FE (đọc password bằng `read -rs`, không ghi vào history hoặc tài liệu).

Sau đó gọi absolute path của `local-wsl/tests/test-live.sh`. Script kiểm API/worker container thật, profile và cap trước inference; chỉ chấp nhận profile OpenAI mặc định đã đối chiếu giá, không stages/model khác hoặc fallback FPT chưa được duyệt. Nếu đã cấu hình FPT key, tắt fallback rõ ràng cho smoke này rồi start lại; không tự sửa `.env` bằng script test. FPT wire-live cần phép riêng, chưa có trong entrypoint.

Guard USD dùng trần bảo thủ theo giới hạn input của model và max completion/calls đã cấu hình, giá tài liệu $0,75 input/$4,5 output mỗi triệu tokens; **không** phải hard billing cap upstream hay ngân sách cho các job khác chạy đồng thời. Muốn ít calls hơn, operator hạ `CATALOGUE_MAX_CALLS` rõ ràng trong env và start lại trước khi chạy; script không tự tăng budget hoặc thử model hàng loạt.

Entry point giữ operation references (không token/ảnh/URL ký) trong `.runtime/`, không auto retry inference và không tự review/commit thay người dùng. Sau phân tích cần mở lại đúng nháp trên FE, review/edit/commit rồi kiểm wardrobe, recommendation, feedback và refresh/stop/start. `no_items` không phải lưu thành công garment. Chưa có đủ phép/key/ảnh/budget trong lượt triển khai này nên live vẫn `NOT TESTED`.
