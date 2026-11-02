import { mkdir, readFile, writeFile, rename, unlink, rmdir, appendFile } from 'node:fs/promises'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { assertRepo, snapshot, same, changed, git, upstream, inspectWorking, inspectHistory, Blocked } from './git.mjs'
import { checks } from './checks.mjs'

const json = async file => JSON.parse(await readFile(file, 'utf8'))
export async function atomic(file, value) {
  await mkdir(path.dirname(file), { recursive: true })
  const temp = `${file}.${randomUUID()}.tmp`
  await writeFile(temp, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 })
  await rename(temp, file)
}
export async function settings(config) {
  try { return await json(path.join(config.stateDirectory, 'settings.json')) } catch (error) {
    if (error.code !== 'ENOENT') throw error
    return { mode: 'dry-run' }
  }
}

// Khóa cả lượt, không chỉ lúc git commit. Phiên cạnh tranh làm cả hai lượt bị chặn.
async function acquire(repo) {
  const common = (await git(repo, ['rev-parse', '--git-common-dir'])).trim()
  const base = path.resolve(repo.directory, common, 'garblo-autogit')
  await mkdir(base, { recursive: true })
  const lock = path.join(base, 'turn.lock')
  const conflict = path.join(base, 'conflict')
  try { await mkdir(lock) } catch (error) {
    if (error.code !== 'EEXIST') throw error
    // Không tự phá khóa còn sống hoặc khóa chưa ghi xong owner.
    let owner
    try { owner = await json(path.join(lock, 'owner.json')) } catch { throw new Blocked('Repo có khóa chưa rõ chủ; kiểm tra thủ công.') }
    let alive = true
    try { process.kill(owner.pid, 0) } catch (failure) { if (failure.code === 'ESRCH') alive = false }
    if (alive) {
      await writeFile(conflict, 'Có lượt làm việc chồng lấn.\n', { mode: 0o600 })
      throw new Blocked('Repo đang có lượt làm việc khác; chặn cả hai lượt.')
    }
    // Chỉ dọn khóa đã biết tên của process không còn sống; không đụng file Git/user.
    await unlink(path.join(lock, 'owner.json'))
    await rmdir(lock)
    await mkdir(lock)
  }
  await unlink(conflict).catch(error => { if (error.code !== 'ENOENT') throw error })
  const token = randomUUID()
  await atomic(path.join(lock, 'owner.json'), { pid: process.pid, token })
  return {
    async check() {
      if ((await json(path.join(lock, 'owner.json'))).token !== token) throw new Blocked('Đã mất khóa repo.')
      try { await readFile(conflict) } catch (error) { if (error.code === 'ENOENT') return; throw error }
      throw new Blocked('Có lượt chồng lấn; không tự commit/push.')
    },
    async release() {
      if ((await json(path.join(lock, 'owner.json'))).token === token) {
        await unlink(path.join(lock, 'owner.json'))
        await rmdir(lock)
      }
    },
  }
}

export class Coordinator {
  constructor(config, { verify = checks, authorize = async () => {}, notify = () => {} } = {}) {
    this.config = config
    this.verify = verify
    this.authorize = authorize
    this.notify = notify
    this.turns = new Map()
    this.pending = new Map()
    this.sequence = Promise.resolve()
  }
  // Hook before/after phải đợi snapshot xong, không chạy fire-and-forget.
  serial(fn) {
    const task = this.sequence.then(fn)
    this.sequence = task.catch(() => {})
    return task
  }
  async report(repo, result) {
    const entry = { at: new Date().toISOString(), repo: repo.id, ...result }
    await atomic(path.join(this.config.stateDirectory, `last-${repo.id}.json`), entry)
    await appendFile(path.join(this.config.stateDirectory, 'audit.jsonl'), JSON.stringify(entry) + '\n', { mode: 0o600 })
    // Nội dung log chỉ gồm mã/trạng thái, không gồm diff/output/prompt.
    this.notify(entry)
    return entry
  }
  async begin({ sessionID, turnID, agent, directory, parentID }) {
    this.cancel(sessionID)
    const pending = this.pending.get(sessionID)
    if (pending) await pending.done
    return this.serial(async () => {
      if (this.turns.has(sessionID)) return
      const repos = this.config.repos.filter(repo => directory === this.config.workspace || directory === repo.directory)
      if (parentID) {
        // Child có thể ghi khi parent đã trả lời: đánh dấu chồng lấn qua khóa dùng chung.
        for (const repo of repos) {
          try { const lock = await acquire(repo); await lock.release() } catch { /* Chủ khóa sẽ đọc conflict. */ }
        }
        return
      }
      if (agent !== 'build' || (await settings(this.config)).mode === 'off') return
      const turn = { sessionID, turnID, repos: new Map(), tools: new Map(), cancelled: false }
      for (const repo of repos) {
        let lock
        try {
          lock = await acquire(repo)
          const baseline = await assertRepo(repo)
          turn.repos.set(repo.id, { repo, lock, baseline, expected: baseline, owned: new Set() })
        } catch (error) {
          if (lock) await lock.release()
          await this.report(repo, { status: 'blocked', reason: error.message })
        }
      }
      this.turns.set(sessionID, turn)
    })
  }
  cancel(sessionID) {
    const pending = this.pending.get(sessionID)
    if (pending) pending.controller.abort()
  }
  async beforeTool(sessionID, callID) {
    this.cancel(sessionID)
    const pending = this.pending.get(sessionID)
    if (pending) await pending.done
    return this.serial(async () => {
      const turn = this.turns.get(sessionID)
      if (!turn) return
      if (turn.tools.size) for (const state of turn.repos.values()) state.blocked = 'Các tool ghi chạy chồng lấn; không xác định được nguồn thay đổi.'
      for (const state of turn.repos.values()) {
        const current = await snapshot(state.repo)
        if (!same(current, state.expected)) state.blocked = 'Có thay đổi ngoài tool/lượt đang theo dõi.'
      }
      turn.tools.set(callID, true)
    })
  }
  async afterTool(sessionID, callID, { background = false } = {}) {
    return this.serial(async () => {
      const turn = this.turns.get(sessionID)
      if (!turn?.tools.has(callID)) return
      for (const state of turn.repos.values()) {
        const current = await snapshot(state.repo)
        if (background) state.blocked = 'Tool chạy nền; không tự commit khi tác vụ có thể còn ghi file.'
        if (current.head !== state.expected.head || current.index !== state.expected.index || current.branch !== state.expected.branch) state.blocked = 'HEAD/index/nhánh đã đổi trong lượt; không gom thay đổi tự động.'
        for (const name of changed(state.expected, current)) {
          if (name in state.baseline.files) state.blocked = 'Tool đã sửa file có thay đổi từ trước; cần commit thủ công.'
          else state.owned.add(name)
        }
        state.expected = current
      }
      turn.tools.delete(callID)
    })
  }
  async abandon(sessionID) {
    this.cancel(sessionID)
    const pending = this.pending.get(sessionID)
    if (pending) await pending.done
    return this.serial(async () => {
      const turn = this.turns.get(sessionID)
      this.turns.delete(sessionID)
      if (turn) for (const state of turn.repos.values()) await state.lock.release()
    })
  }
  finish(sessionID) {
    if (this.pending.has(sessionID)) return this.pending.get(sessionID).done
    const controller = new AbortController()
    const done = this.serial(async () => {
      const turn = this.turns.get(sessionID)
      this.turns.delete(sessionID) // Event lặp/reload không được nhận lại quyền sở hữu.
      if (!turn) return []
      const results = []
      try {
        for (const state of turn.repos.values()) {
          try {
            if (controller.signal.aborted) throw new Blocked('Đã có yêu cầu mới; hủy automation.')
            if (turn.tools.size || state.blocked) throw new Blocked(state.blocked ?? 'Còn tool đang chạy.')
            await state.lock.check()
            if (!same(await snapshot(state.repo), state.expected)) throw new Blocked('File thay đổi sau tool cuối; không commit.')
            const names = [...state.owned].filter(name => name in state.expected.files).sort()
            const mode = (await settings(this.config)).mode
            if (mode === 'off') { results.push(await this.report(state.repo, { status: 'off' })); continue }
            if (!names.length) { results.push(await this.report(state.repo, { status: 'no-change' })); continue }
            await inspectWorking(state.repo, names)
            if (mode === 'dry-run') {
              const remote = await upstream(state.repo)
              results.push(await this.report(state.repo, { status: 'dry-run', files: names.length, ahead: remote.count, wouldCheck: remote.count + Number(names.length > 0) >= this.config.threshold }))
              continue
            }
            if (mode !== 'on') throw new Blocked('Mode không hợp lệ; không chạy Git.')
            let commit
            if (names.length) {
              await this.authorize('commit', state.repo, turn)
              await state.lock.check()
              if (controller.signal.aborted || !same(await snapshot(state.repo), state.expected)) throw new Blocked('Trạng thái đổi trước commit.')
              await git(state.repo, ['add', '--', ...names])
              await git(state.repo, ['diff', '--cached', '--check'])
              const staged = (await git(state.repo, ['diff', '--cached', '--name-only', '-z'])).split('\0').filter(Boolean).sort()
              if (!same(staged, names)) throw new Blocked('Index chứa thay đổi ngoài phạm vi; không commit.')
              const label = names.every(name => /\.(md|rst|txt)$/.test(name)) ? 'docs' : 'chore'
              const summary = names.slice(0, 2).map(name => path.basename(name)).join(', ')
              const message = `${label}(${state.repo.id.toLowerCase()}): cập nhật ${summary}${names.length > 2 ? ` và ${names.length - 2} tệp khác` : ''}`
              await git(state.repo, ['commit', '-m', message, '-m', `OpenCode-Turn: ${turn.turnID}\nOpenCode-Session: ${turn.sessionID}`])
              commit = (await snapshot(state.repo)).head
              await this.report(state.repo, { status: 'committed', commit, files: names.length })
            }
            results.push(await this.gate(state.repo, { turn, signal: controller.signal, lock: state.lock, commit }))
          } catch (error) {
            results.push(await this.report(state.repo, { status: 'blocked', reason: error.message }))
          }
        }
      } finally {
        for (const state of turn.repos.values()) await state.lock.release()
      }
      return results
    }).finally(() => this.pending.delete(sessionID))
    this.pending.set(sessionID, { controller, done })
    return done
  }
  async gate(repo, { turn, signal, lock, commit } = {}) {
    const mode = (await settings(this.config)).mode
    if (mode !== 'on') throw new Blocked('Push chỉ chạy khi mode=on.')
    const initial = await assertRepo(repo)
    if (Object.keys(initial.files).length) throw new Blocked('Working tree còn thay đổi; giữ commit local, chưa push.')
    await this.authorize('fetch', repo, turn)
    const remote = await upstream(repo, { fetch: true, signal })
    if (remote.count < this.config.threshold) return this.report(repo, { status: 'local', ahead: remote.count, commit })
    await this.authorize('check', repo, turn)
    const names = await inspectHistory(repo, remote.ref)
    await this.verify(repo, names, { signal })
    if (signal?.aborted || (await settings(this.config)).mode !== 'on' || !same(await snapshot(repo), initial)) throw new Blocked('HEAD/file/mode đổi sau kiểm tra; hủy push.')
    await lock?.check()
    await this.authorize('push', repo, turn)
    // Fetch lần nữa để không push một lịch sử không còn fast-forward sau khi test lâu.
    await upstream(repo, { fetch: true, signal })
    await lock?.check()
    if (signal?.aborted || (await settings(this.config)).mode !== 'on' || !same(await snapshot(repo), initial)) throw new Blocked('Trạng thái đổi ngay trước push.')
    // Dùng SHA đã kiểm tra, không dùng HEAD động; Git tự từ chối non-fast-forward.
    await git(repo, ['push', '--no-follow-tags', '--recurse-submodules=no', remote.remote, `${initial.head}:${remote.merge}`], { signal, timeout: 60_000 })
    const actual = (await git(repo, ['ls-remote', remote.remote, remote.merge], { signal, timeout: 60_000 })).split(/\s/)[0]
    if (actual !== initial.head) throw new Blocked('Chưa xác nhận được SHA trên remote sau push.')
    return this.report(repo, { status: 'pushed', commit: initial.head, count: remote.count })
  }
  async retry(repo, turn) {
    const lock = await acquire(repo)
    try { return await this.gate(repo, { lock, turn }) } finally { await lock.release() }
  }
  async close() {
    for (const sessionID of this.pending.keys()) this.cancel(sessionID)
    await Promise.allSettled([...this.pending.values()].map(p => p.done))
    for (const sessionID of [...this.turns.keys()]) await this.abandon(sessionID)
  }
}
