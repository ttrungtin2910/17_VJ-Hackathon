# AutoGit cho Garblo / OpenCode V2

Sau mỗi **lượt yêu cầu hoàn tất thành công bằng agent `build`**, plugin tạo commit
local cho thay đổi theo dõi được qua tool. FE và BE độc lập: **ít nhất 3 commit
chưa push trên upstream** mới chạy kiểm tra cả batch và push. Commit thủ công
được tính. Không có thay đổi thuộc lượt thì không commit/fetch/push.

## Cài và bật

Yêu cầu Node >=24, Git đã cấu hình author/SSH và OpenCode **2.2.x** (kiểm tra với
2.2.2). Chạy tại `garblo-backend/`:

```bash
npm --prefix tools/opencode-autogit ci --ignore-scripts
npm --prefix tools/opencode-autogit test
npm --prefix tools/opencode-autogit run check
node tools/opencode-autogit/cli.mjs install
node tools/opencode-autogit/cli.mjs status
```

Installer tạo `.opencode/opencode.json` **tại workspace cha**, mặc định `dry-run`.
Nó không ghi đè config hiện hữu, không thay global config, không mở quyền shell
tổng quát. Nếu đã có config, tự merge entry plugin và các quyền có phạm vi từ
`install.mjs`. OpenCode tự reload config; có thể dùng `/reload` nếu cần.

Sau khi đã nghiệm thu dry-run, bật:

```bash
node tools/opencode-autogit/cli.mjs mode on
```

Workspace cha không phải Git repo: cấu hình được tạo và state local không thuộc
commit BE. Source, installer, config allowlist và test nằm trong repo BE này để
cài lại được. Không bật plugin trước khi hoàn tất review code automation.

## Điều khiển

Trong OpenCode:

- `/autogit-status`: trạng thái gần nhất, không gọi model.
- `/autogit-mode off`: tạm dừng; `dry-run`: chỉ mô phỏng; `on`: bật commit/push.
- `/autogit-retry FE` hoặc `/autogit-retry BE`: kiểm tra/push lại **commit có sẵn**;
  không stage file, không vượt ngưỡng 3. Chỉ agent `build` được chạy.

CLI tương đương, ví dụ từ repo BE:

```bash
node tools/opencode-autogit/cli.mjs status
node tools/opencode-autogit/cli.mjs mode dry-run
node tools/opencode-autogit/cli.mjs mode off
node tools/opencode-autogit/cli.mjs retry BE
```

CLI là thao tác trực tiếp của operator; plugin kiểm tra các quyền
`garblo.autogit.commit/fetch/check/push` riêng cho từng đường dẫn repo. Các quyền
này là capability riêng của plugin, **không** phải cấp quyền `shell: git *` cho
agent. Muốn cấm automation bằng policy, deny các action này hoặc disable plugin.
`garblo.autogit.configure` chỉ dùng để đổi mode tại workspace.

Allowlist mặc định (`config.mjs`):

| Repo | Remote | Nhánh |
|---|---|---|
| FE | `Thinhjp/Garblo-Frontend` | `main` |
| BE | `ttrungtin2910/17_VJ-Hackathon` | `rebuild/openai-fpt-ai-factory` |

Nhánh khác, upstream khác hoặc push URL khác đều bị chặn. Muốn đổi nhánh phải
review/sửa allowlist rõ ràng; không tự phát hiện rồi cấp quyền cho mọi nhánh.

## Kiểm tra trước push

- Docs-only (`.md/.rst/.txt`): whitespace của từng commit, mẫu credential/conflict,
  kiểm tra đích link Markdown tương đối còn tồn tại. Không kiểm anchor/URL mạng.
- FE: Node >=24; `typecheck`, `lint`, unit test, build và E2E demo.
- BE: Python >=3.12; unit non-integration, Ruff lint/format và 14 test script local.
  Ưu tiên `.venv/bin/python` trong BE, nếu không có dùng `python3` từ PATH của server.
- Thay đổi automation: thêm kiểm tra cú pháp và Node test của package này. Nếu
  batch chỉ gồm automation + tài liệu, không cần Python/backend runtime.
- Hạ tầng FE: thêm real E2E, bắt buộc `GARBLO_E2E_BASE_URL` tới stack test riêng.
- Hạ tầng/migration/local-wsl BE: validate Compose bằng sample/test env; chạy
  integration với JUnit và chặn nếu có skip/không có test. Cần đủ
  `GARBLO_TEST_DATABASE_URL`, `GARBLO_TEST_S3_ENDPOINT_URL`,
  `GARBLO_TEST_S3_ACCESS_KEY`, `GARBLO_TEST_S3_SECRET_KEY`, `GARBLO_TEST_API_URL`
  của tài nguyên test riêng. Integration có thể tạo/xóa tài nguyên **test**.

Thiếu dependency/browser/test env hoặc lệnh exit khác 0 => **giữ commit local,
chặn push**. Không tự cài thư viện, tự sửa code hay bỏ qua test/hook để đẩy lên.
Không tự bật/tắt container, không migrate DB đang dùng, không gọi AI thật.
Readiness/mocks không chứng minh inference live.

## An toàn và giới hạn

- Baseline chụp trước model request đầu lượt. File dirty/staged từ trước không
  được nhận làm thay đổi của lượt. Nếu tool sửa tiếp file dirty, chặn cả repo.
- Tool `patch/edit/write/shell/bash/execute` đồng bộ được theo dõi before/after.
  Thay đổi ngoài cửa sổ tool, Git HEAD/index đổi trong lượt, tool nền, tool ghi
  chồng lấn hoặc child/phiên cùng repo làm automation bị chặn.
- **Không chạy editor/process ngoài OpenCode cùng ghi repo trong cửa sổ một tool.**
  Snapshot không thể chứng minh tác giả của hai lần ghi ngoài hệ thống cùng lúc.
  Nên dùng checkout/worktree riêng nếu làm song song; worktree không có trong
  allowlist mặc định thì không tự chạy.
- Không chạy trong Plan/custom agent, khi bị lỗi/hủy, đang chờ tool, hoặc không
  quan sát được đầy đủ đầu lượt (ví dụ vừa cài/reload giữa lượt).
- Giữ khóa repo xuyên lượt, dùng Git common dir để phát hiện nhiều instance.
  Chủ khóa còn sống không bị tự phá. Nếu crash trước khi ghi xong owner, kiểm tra
  thủ công thư mục `garblo-autogit/turn.lock` dưới Git common dir; không xóa khóa
  khi còn phiên đang làm việc. Không đụng `.git/index.lock` của Git.
- Chặn `.env` thật, credential có mẫu phổ biến, symlink/submodule, binary, file
  >2 MiB, artifact/log. `.env.example/.env.sample` và test env của local-wsl chỉ
  được phép nếu content vẫn qua scanner. Scanner **không thay thế secret scanner
  chuyên dụng hoặc review bảo mật**; nên giữ hook của tổ chức.
- Quét **mọi commit chưa push**, kể cả credential đã bị xóa ở commit kế tiếp.
  Commit merge/root chưa push cần review thủ công.
- Commit bằng Git bình thường, không `--no-verify`; hook lỗi có thể để lại index
  đã stage đúng các file của lượt. Không tự reset/unstage: xem `git diff --cached`
  và xử lý thủ công rồi thử lại.
- Fetch trước đếm và trước push; remote phải là tổ tiên HEAD. Không tự pull,
  merge, rebase, amend, force-push hoặc push tag. Push **SHA đã kiểm tra**, không
  dùng HEAD động. Git vẫn từ chối nếu remote đổi giữa fetch và push.
- Thay đổi mới/mode off/Plan khi đang kiểm tra làm kết quả hết hiệu lực. Mỗi lệnh
  timeout tối đa 10 phút (network Git 60 giây); không retry vòng lặp vô hạn.
- Kết quả đến **sau** câu trả lời cuối của agent, không nhất thiết nằm trong câu
  trả lời đó. Xem `/autogit-status` hoặc CLI; thông báo cũng được đưa vào context
  lượt sau, không tạo prompt mới gây lặp.
- State nằm ở `.opencode/autogit/`: `settings.json`, `last-FE.json`, `last-BE.json`,
  `audit.jsonl`. Không log diff, prompt, stdout/stderr test, token hoặc signed URL.
  Nhật ký append-only; operator có thể lưu trữ/xoay vòng khi cần.
- Đóng terminal/kill server không phải tín hiệu thành công. Lượt chưa hoàn tất
  không tự commit; sau restart không đoán quyền sở hữu các thay đổi còn lại.

## Bằng chứng nghiệm thu

`npm test` dùng repo tạm và **remote bare local**, không push GitHub, không gọi AI
hay DB/S3. Có test tích hợp Git cho ngưỡng 1/2/3, FE/BE riêng, dirty/staged,
secret lịch sử, khóa giữa instance, fail/cancel/retry, hook và phân kỳ. Test
plugin mô phỏng context/event theo API 2.2.2; kiểm tra plugin load trên server
thật là bước riêng, không coi test mock là end-to-end với model.

Tài liệu API: [plugin server OpenCode V2](https://opencode.ai/v2/docs/build/plugins/server).
