import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, rm, chmod } from 'node:fs/promises'
import path from 'node:path'
import { Coordinator, atomic } from '../coordinator.mjs'
import { git, run, snapshot, safeContent, safeName, upstream, inspectHistory } from '../git.mjs'
import { checkLinks, docsOnly, infra, checks } from '../checks.mjs'

async function fixture(t, { count = 1, mode = 'on', verify } = {}) {
  const root = await mkdtemp('/tmp/opencode/garblo-autogit-test-')
  t.after(() => rm(root, { recursive: true, force: true }))
  const repos = []
  for (let i = 0; i < count; i++) {
    const bare = path.join(root, `remote-${i}.git`)
    const directory = path.join(root, `repo ${i}`)
    await mkdir(directory)
    await run(root, 'git', ['init', '--bare', '--initial-branch=main', bare])
    await run(directory, 'git', ['init', '--initial-branch=main'])
    const repo = { id: i === 0 ? 'FE' : 'BE', directory, remote: 'origin', url: bare, branches: ['main'] }
    await git(repo, ['config', 'user.name', 'AutoGit Test'])
    await git(repo, ['config', 'user.email', 'autogit@example.invalid'])
    await writeFile(path.join(directory, 'README.md'), '# Kiểm thử\n')
    await git(repo, ['add', 'README.md'])
    await git(repo, ['commit', '-m', 'init'])
    await git(repo, ['remote', 'add', 'origin', bare])
    await git(repo, ['push', '--set-upstream', 'origin', 'main'])
    repos.push(repo)
  }
  const config = { workspace: root, stateDirectory: path.join(root, 'state'), threshold: 3, repos }
  await atomic(path.join(config.stateDirectory, 'settings.json'), { mode })
  const calls = []
  const coordinator = new Coordinator(config, { verify: verify ?? (async (repo, names) => { calls.push({ repo: repo.id, names }) }) })
  t.after(() => coordinator.close())
  const begin = (id = 'session', directory = root, agent = 'build', parentID) => coordinator.begin({ sessionID: id, turnID: `turn-${Math.random()}`, directory, agent, parentID })
  const edit = async (repo, name, content, { sessionID = 'session', background = false } = {}) => {
    await coordinator.beforeTool(sessionID, 'tool')
    await writeFile(path.join(repo.directory, name), content)
    await coordinator.afterTool(sessionID, 'tool', { background })
  }
  const turn = async (repo, name, content) => { await begin(); await edit(repo, name, content); return coordinator.finish('session') }
  return { root, config, repos, coordinator, begin, edit, turn, calls }
}

test('commit 1–2 giữ local, commit 3 kiểm tra toàn batch và push đúng SHA', async t => {
  const { repos: [repo], turn, calls } = await fixture(t)
  for (let i = 1; i <= 3; i++) {
    const [result] = await turn(repo, `file-${i}.md`, `# Lượt ${i}\n`)
    assert.equal(result.status, i < 3 ? 'local' : 'pushed')
    assert.equal(calls.length, i < 3 ? 0 : 1)
  }
  assert.deepEqual(calls[0].names.sort(), ['file-1.md', 'file-2.md', 'file-3.md'])
  assert.equal((await upstream(repo)).count, 0)
  assert.equal(Object.keys((await snapshot(repo)).files).length, 0)
})

test('FE/BE đếm độc lập, xử lý FE trước BE', async t => {
  const { repos: [fe, be], turn, begin, edit, coordinator, calls } = await fixture(t, { count: 2 })
  await turn(fe, 'one.md', '# FE 1\n')
  await turn(be, 'one.md', '# BE 1\n')
  await begin()
  await edit(fe, 'two.md', '# FE 2\n')
  await edit(be, 'two.md', '# BE 2\n')
  const results = await coordinator.finish('session')
  assert.deepEqual(results.map(r => r.repo), ['FE', 'BE'])
  assert.equal((await upstream(fe)).count, 2)
  assert.equal((await upstream(be)).count, 2)
  assert.equal(calls.length, 0)
})

test('không thay đổi, Plan hoặc custom agent: không commit/fetch', async t => {
  const { repos: [repo], begin, coordinator } = await fixture(t)
  const initial = (await snapshot(repo)).head
  await begin()
  assert.equal((await coordinator.finish('session'))[0].status, 'no-change')
  for (const agent of ['plan', 'general']) {
    await begin('session', repo.directory, agent)
    assert.deepEqual(await coordinator.finish('session'), [])
  }
  assert.equal((await snapshot(repo)).head, initial)
})

test('file có sẵn không bị gom; chỉ file thuộc lượt vào commit', async t => {
  const { repos: [repo], begin, edit, coordinator } = await fixture(t)
  await writeFile(path.join(repo.directory, 'user.md'), 'Thay đổi của người dùng\n')
  await begin()
  await edit(repo, 'ours.md', '# Của lượt này\n')
  const [result] = await coordinator.finish('session')
  assert.equal(result.status, 'blocked') // Commit local được giữ, push bị dirty tree chặn.
  assert.equal((await git(repo, ['show', '--format=', '--name-only', 'HEAD'])).trim(), 'ours.md')
  assert.equal(await readFile(path.join(repo.directory, 'user.md'), 'utf8'), 'Thay đổi của người dùng\n')
})

test('đụng file đã dirty từ trước: không commit cả lượt', async t => {
  const { repos: [repo], begin, edit, coordinator } = await fixture(t)
  await writeFile(path.join(repo.directory, 'README.md'), 'Đang sửa\n')
  const initial = (await snapshot(repo)).head
  await begin()
  await edit(repo, 'README.md', 'Sửa tiếp\n')
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  assert.equal((await snapshot(repo)).head, initial)
})

test('staged của người dùng và HEAD thay đổi trong tool đều chặn', async t => {
  const { repos: [repo], begin, coordinator } = await fixture(t)
  await writeFile(path.join(repo.directory, 'staged.md'), '# Giữ staged\n')
  await git(repo, ['add', 'staged.md'])
  await begin()
  assert.deepEqual(await coordinator.finish('session'), [])
  assert.equal((await snapshot(repo)).staged, true)
  await git(repo, ['commit', '-m', 'manual'])
  await begin()
  await coordinator.beforeTool('session', 'manual')
  await writeFile(path.join(repo.directory, 'manual.md'), '# Manual\n')
  await git(repo, ['add', 'manual.md'])
  await git(repo, ['commit', '-m', 'manual 2'])
  await coordinator.afterTool('session', 'manual')
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
})

test('dry-run/off không stage, không commit và không push', async t => {
  const { repos: [repo], turn, config } = await fixture(t, { mode: 'dry-run' })
  const initial = (await snapshot(repo)).head
  assert.equal((await turn(repo, 'preview.md', '# Preview\n'))[0].status, 'dry-run')
  assert.equal((await snapshot(repo)).head, initial)
  assert.equal((await snapshot(repo)).staged, false)
  await atomic(path.join(config.stateDirectory, 'settings.json'), { mode: 'off' })
  assert.deepEqual(await turn(repo, 'off.md', '# Off\n'), [])
  assert.equal((await snapshot(repo)).head, initial)
})

test('tool nền, tool chồng lấn và sửa ngoài tool đều không commit', async t => {
  const { repos: [repo], begin, edit, coordinator } = await fixture(t)
  const initial = (await snapshot(repo)).head
  await begin()
  await edit(repo, 'background.md', '# Nền\n', { background: true })
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  await begin()
  await coordinator.beforeTool('session', 'first')
  await coordinator.beforeTool('session', 'second')
  await writeFile(path.join(repo.directory, 'parallel.md'), '# Song song\n')
  await coordinator.afterTool('session', 'first')
  await coordinator.afterTool('session', 'second')
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  await begin()
  await writeFile(path.join(repo.directory, 'external.md'), '# Ngoài tool\n')
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  assert.equal((await snapshot(repo)).head, initial)
})

test('hai coordinator/phiên cùng repo chặn cả hai; lượt tiếp theo lấy lại khóa', async t => {
  const { config, repos: [repo], begin, edit, coordinator } = await fixture(t)
  const second = new Coordinator(config)
  t.after(() => second.close())
  await begin()
  await edit(repo, 'first.md', '# First\n')
  await second.begin({ sessionID: 'second', turnID: '2', agent: 'build', directory: repo.directory })
  assert.deepEqual(await second.finish('second'), [])
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  await begin()
  await edit(repo, 'next.md', '# Next\n')
  await coordinator.finish('session')
  assert.equal((await git(repo, ['show', '--format=', '--name-only', 'HEAD'])).trim(), 'next.md')
})

test('event lặp và reload không tạo commit trùng, failed/interrupted không commit', async t => {
  const { repos: [repo], begin, edit, coordinator, config } = await fixture(t)
  await begin()
  await edit(repo, 'success.md', '# Success\n')
  await Promise.all([coordinator.finish('session'), coordinator.finish('session')])
  assert.equal((await upstream(repo)).count, 1)
  assert.deepEqual(await coordinator.finish('session'), [])
  const restarted = new Coordinator(config)
  assert.deepEqual(await restarted.finish('session'), [])
  await begin()
  await edit(repo, 'failed.md', '# Failed\n')
  await coordinator.abandon('session')
  assert.deepEqual(await coordinator.finish('session'), [])
  assert.equal((await upstream(repo)).count, 1)
})

test('test fail tại ngưỡng: giữ 3 commit local; retry thành công không tạo commit mới', async t => {
  const { repos: [repo], turn, coordinator } = await fixture(t, { verify: async () => { throw new Error('Test thất bại') } })
  await turn(repo, 'one.md', '# 1\n')
  await turn(repo, 'two.md', '# 2\n')
  assert.equal((await turn(repo, 'three.md', '# 3\n'))[0].status, 'blocked')
  assert.equal((await upstream(repo)).count, 3)
  const initial = (await snapshot(repo)).head
  coordinator.verify = async () => {}
  assert.equal((await coordinator.retry(repo)).status, 'pushed')
  assert.equal((await snapshot(repo)).head, initial)
})

test('thay file/HEAD trong kiểm tra hoặc đổi mode chặn push', async t => {
  const { repos: [repo], turn, coordinator, config } = await fixture(t)
  await turn(repo, 'one.md', '# 1\n')
  await turn(repo, 'two.md', '# 2\n')
  coordinator.verify = async () => { await writeFile(path.join(repo.directory, 'changed.md'), '# Đổi khi test\n') }
  assert.equal((await turn(repo, 'three.md', '# 3\n'))[0].status, 'blocked')
  assert.equal((await upstream(repo)).count, 3)
  await rm(path.join(repo.directory, 'changed.md'))
  coordinator.verify = async () => atomic(path.join(config.stateDirectory, 'settings.json'), { mode: 'off' })
  await assert.rejects(coordinator.retry(repo), /mode|đổi/)
  assert.equal((await upstream(repo)).count, 3)
})

test('yêu cầu mới hủy kiểm tra; không push dù test cuối cùng trả về thành công', async t => {
  const { repos: [repo], turn, begin, edit, coordinator } = await fixture(t)
  await turn(repo, 'one.md', '# 1\n')
  await turn(repo, 'two.md', '# 2\n')
  let entered
  const started = new Promise(resolve => { entered = resolve })
  coordinator.verify = async (_repo, _names, { signal }) => {
    entered()
    await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }))
  }
  await begin()
  await edit(repo, 'three.md', '# 3\n')
  const finishing = coordinator.finish('session')
  await started
  coordinator.cancel('session')
  assert.equal((await finishing)[0].status, 'blocked')
  assert.equal((await upstream(repo)).count, 3)
})

test('nhánh/upstream/URL lạ bị chặn, remote phân kỳ không tự merge', async t => {
  const { repos: [repo], turn } = await fixture(t)
  await turn(repo, 'local.md', '# Local\n')
  await git(repo, ['config', 'remote.origin.pushurl', '/tmp/not-allowed'])
  await assert.rejects(upstream(repo), /URL/)
  await git(repo, ['config', '--unset', 'remote.origin.pushurl'])
  await git(repo, ['config', 'branch.main.merge', 'refs/heads/other'])
  await assert.rejects(upstream(repo), /Upstream/)
  await git(repo, ['config', 'branch.main.merge', 'refs/heads/main'])
  const peer = path.join(path.dirname(repo.directory), 'peer')
  await run(path.dirname(repo.directory), 'git', ['clone', repo.url, peer])
  const other = { directory: peer }
  await git(other, ['config', 'user.name', 'Peer'])
  await git(other, ['config', 'user.email', 'peer@example.invalid'])
  await writeFile(path.join(peer, 'peer.md'), '# Peer\n')
  await git(other, ['add', 'peer.md'])
  await git(other, ['commit', '-m', 'peer'])
  await git(other, ['push', 'origin', 'main'])
  const [result] = await turn(repo, 'second.md', '# Second\n')
  assert.equal(result.status, 'blocked')
  assert.equal((await git(repo, ['log', '-1', '--format=%s'])).trim().startsWith('docs(fe)'), true)
})

test('hook commit/push không bị bypass và push lỗi giữ commit local', async t => {
  const { repos: [repo], turn } = await fixture(t)
  const hook = path.join(repo.directory, '.git/hooks/pre-push')
  await writeFile(hook, '#!/bin/sh\nexit 1\n')
  await chmod(hook, 0o700)
  await turn(repo, 'one.md', '# 1\n')
  await turn(repo, 'two.md', '# 2\n')
  assert.equal((await turn(repo, 'three.md', '# 3\n'))[0].status, 'blocked')
  assert.equal((await upstream(repo)).count, 3)
})

test('bảo vệ credential/conflict/artifact và quét cả commit trung gian', async t => {
  for (const name of ['.env', '.env.production', 'id_rsa', 'dist/bundle.js', 'test-results/a.md', '../escape', 'secrets.json']) assert.throws(() => safeName(name))
  for (const text of ['<<<<<<< HEAD\na\n=======\nb\n>>>>>>> branch\n', 'sk-' + 'a'.repeat(32), 'OPENAI_API_KEY=actual-sensitive-value']) assert.throws(() => safeContent('file.txt', text))
  safeContent('.env.example', 'OPENAI_API_KEY=\nPASSWORD=garblo-local-only\n')
  const { repos: [repo] } = await fixture(t)
  await writeFile(path.join(repo.directory, 'bad.md'), 'sk-' + 'a'.repeat(32))
  await git(repo, ['add', 'bad.md'])
  await git(repo, ['commit', '-m', 'manual secret'])
  await writeFile(path.join(repo.directory, 'bad.md'), '# Đã bỏ secret\n')
  await git(repo, ['add', 'bad.md'])
  await git(repo, ['commit', '-m', 'remove secret'])
  await assert.rejects(inspectHistory(repo, 'origin/main'), /credential/)
})

test('file có credential không bị stage hoặc commit', async t => {
  const { repos: [repo], turn } = await fixture(t)
  const initial = (await snapshot(repo)).head
  assert.equal((await turn(repo, 'bad.md', 'ghp_' + 'a'.repeat(32)))[0].status, 'blocked')
  const after = await snapshot(repo)
  assert.equal(after.head, initial)
  assert.equal(after.staged, false)
})

test('docs-only kiểm link thật, bỏ ví dụ code; profile và thiếu executable chặn', async t => {
  const { repos: [repo] } = await fixture(t)
  assert.equal(docsOnly(['docs/a.md', 'README.md']), true)
  assert.equal(docsOnly(['docs/a.md', 'src/code.py']), false)
  assert.equal(infra(['migrations/versions/new.py']), true)
  await writeFile(path.join(repo.directory, 'links.md'), '[Có](README.md)\n```md\n[Ví dụ](not-found.md)\n```\n')
  await checkLinks(repo, ['links.md'])
  await writeFile(path.join(repo.directory, 'links.md'), '[Sai](not-found.md)\n')
  await assert.rejects(checkLinks(repo, ['links.md']), /Link nội bộ/)
  await assert.rejects(checks({ ...repo, checks: [['garblo-nonexistent-command-123']] }, ['src.js']), /Không chạy/)
})

test('child cùng repo đánh dấu parent bị chặn; quyền bị từ chối không stage', async t => {
  const { repos: [repo], begin, edit, coordinator } = await fixture(t)
  await begin()
  await edit(repo, 'parent.md', '# Parent\n')
  await begin('child', repo.directory, 'build', 'session')
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  await begin()
  await edit(repo, 'another.md', '# Another\n')
  coordinator.authorize = async () => { throw new Error('Quyền bị từ chối') }
  assert.equal((await coordinator.finish('session'))[0].status, 'blocked')
  assert.equal((await snapshot(repo)).staged, false)
  assert.equal((await upstream(repo)).count, 0)
})

test('pre-commit lỗi không bị bỏ qua; giữ index của lượt để operator kiểm tra', async t => {
  const { repos: [repo], turn } = await fixture(t)
  const hook = path.join(repo.directory, '.git/hooks/pre-commit')
  await writeFile(hook, '#!/bin/sh\nexit 1\n')
  await chmod(hook, 0o700)
  const initial = (await snapshot(repo)).head
  assert.equal((await turn(repo, 'hook.md', '# Hook\n'))[0].status, 'blocked')
  assert.equal((await snapshot(repo)).head, initial)
  assert.equal((await snapshot(repo)).staged, true)
})

test('push.followTags không làm tag đi kèm; scanner xử lý tên có khoảng trắng', async t => {
  const { repos: [repo], turn } = await fixture(t)
  await turn(repo, 'one space.md', '# 1\n')
  await git(repo, ['tag', '-a', 'not-to-push', '-m', 'Private tag'])
  await git(repo, ['config', 'push.followTags', 'true'])
  await turn(repo, 'two.md', '# 2\n')
  assert.equal((await turn(repo, 'three.md', '# 3\n'))[0].status, 'pushed')
  assert.equal((await git(repo, ['ls-remote', '--tags', 'origin'])).trim(), '')
})

test('tên file dạng Git pathspec luôn được coi là literal, không stage nhầm file khác', async t => {
  const { repos: [repo], begin, edit, coordinator } = await fixture(t)
  await writeFile(path.join(repo.directory, 'user.md'), '# Của người dùng\n')
  await begin()
  await edit(repo, ':(glob)*.md', '# Literal\n')
  await coordinator.finish('session')
  assert.equal((await git(repo, ['show', '--format=', '--name-only', 'HEAD'])).trim(), ':(glob)*.md')
  assert.equal((await snapshot(repo)).staged, false)
  assert.equal((await snapshot(repo)).files['user.md'] !== undefined, true)
})
