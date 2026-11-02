import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { lstat, readFile, access } from 'node:fs/promises'
import path from 'node:path'

export class Blocked extends Error {}

// Không đưa stdout/stderr của Git/test vào log: có thể chứa credential hoặc dữ liệu riêng tư.
export function run(cwd, command, args = [], { signal, timeout = 600_000, env = {} } = {}) {
  return new Promise((resolve, reject) => {
    const environment = { ...process.env, GIT_TERMINAL_PROMPT: '0', GCM_INTERACTIVE: 'never', ...env }
    for (const name of ['GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR', 'GIT_NAMESPACE', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_PARAMETERS', 'GIT_GLOB_PATHSPECS', 'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS']) delete environment[name]
    environment.GIT_LITERAL_PATHSPECS = '1'
    const child = spawn(command, args, {
      cwd, signal, detached: process.platform !== 'win32',
      env: environment,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const chunks = []
    let bytes = 0
    let escalation
    const stop = () => {
      try { process.kill(-child.pid, 'SIGTERM') } catch { child.kill('SIGTERM') }
      escalation ??= setTimeout(() => {
        try { process.kill(-child.pid, 'SIGKILL') } catch { child.kill('SIGKILL') }
      }, 2000)
    }
    signal?.addEventListener('abort', stop, { once: true })
    const timer = setTimeout(stop, timeout)
    child.stdout.on('data', data => {
      bytes += data.length
      if (bytes > 16 * 1024 * 1024) stop()
      else chunks.push(data)
    })
    child.stderr.resume()
    child.on('error', () => {
      clearTimeout(timer)
      clearTimeout(escalation)
      signal?.removeEventListener('abort', stop)
      reject(new Blocked(`Không chạy được ${path.basename(command)} (thiếu executable hoặc đã hủy).`))
    })
    child.on('close', code => {
      clearTimeout(timer)
      clearTimeout(escalation)
      signal?.removeEventListener('abort', stop)
      if (code !== 0 || signal?.aborted) reject(new Blocked(`${path.basename(command)} không đạt (exit ${code ?? 'signal'}).`))
      else resolve(Buffer.concat(chunks).toString('utf8'))
    })
  })
}

export const git = (repo, args, options) => run(repo.directory, 'git', args, options)
export const hash = value => createHash('sha256').update(value).digest('hex')
const nul = text => text.split('\0').filter(Boolean)

export async function snapshot(repo) {
  const head = (await git(repo, ['rev-parse', 'HEAD'])).trim()
  const branch = (await git(repo, ['symbolic-ref', '--quiet', '--short', 'HEAD'])).trim()
  const index = await git(repo, ['diff', '--cached', '--binary', '--no-ext-diff'])
  const names = new Set([
    ...nul(await git(repo, ['diff', '--name-only', '-z', '--no-renames', 'HEAD'])),
    ...nul(await git(repo, ['ls-files', '--others', '--exclude-standard', '-z'])),
  ])
  const files = {}
  for (const name of [...names].sort()) {
    const full = path.join(repo.directory, name)
    try {
      const stat = await lstat(full)
      files[name] = stat.isSymbolicLink() || !stat.isFile() ? 'unsupported' : hash(Buffer.concat([Buffer.from(String(stat.mode)), await readFile(full)]))
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
      files[name] = 'deleted'
    }
  }
  return { head, branch, index: hash(index), staged: index.length > 0, files }
}

export const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)
export function changed(a, b) {
  return [...new Set([...Object.keys(a.files), ...Object.keys(b.files)])].filter(name => a.files[name] !== b.files[name])
}

export function safeName(name) {
  if (!name || path.isAbsolute(name) || name.split('/').includes('..') || /[\x00-\x1f\x7f]/.test(name)) throw new Blocked('Đường dẫn không an toàn.')
  const base = path.posix.basename(name)
  const example = /^\.env\.(example|sample)$/.test(base) || name === 'local-wsl/tests/env.test'
  if ((!example && /^\.?env(?:\.|$)/i.test(base)) || /\.(pem|key|p12|pfx|log|zip|sqlite|db)$/i.test(base)
    || /(^|\/)(?:\.git|node_modules|dist|coverage|test-results|playwright-report|\.runtime|\.venv|__pycache__)(\/|$)/.test(name)
    || /(^|\/)(?:id_rsa|id_ed25519|credentials|secrets?)(\.|\/|$)/i.test(name)) throw new Blocked('Có file nhạy cảm hoặc artifact; cần xử lý thủ công.')
}

export function safeContent(name, content) {
  safeName(name)
  if (content.length > 2 * 1024 * 1024 || content.includes('\0')) throw new Blocked('File nhị phân/quá lớn; cần review thủ công.')
  if (/^(?:<{7}|={7}|>{7})(?: |$)/m.test(content)) throw new Blocked('Có dấu conflict chưa giải quyết.')
  if (/(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{25,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)/.test(content)) throw new Blocked('Phát hiện mẫu credential; không commit/push.')
  // Chặn cả key không có prefix chuẩn. Chỉ chấp nhận placeholder/local-only rõ ràng.
  for (const line of content.split('\n')) {
    const match = line.match(/^\s*(?:export\s+)?[A-Z_]*(?:API_KEY|SECRET_KEY|PASSWORD|ACCESS_TOKEN)\s*=\s*["']?([^"'\s#]+)/)
    if (match && !/^(?:\$|\{|change|your|example|test|garblo|local|dev|unsafe|<)/i.test(match[1])) throw new Blocked('Giá trị secret không phải placeholder; cần review thủ công.')
  }
}

export async function inspectWorking(repo, names) {
  if (!names.length) return
  for (const name of names) {
    safeName(name)
    try {
      const stat = await lstat(path.join(repo.directory, name))
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Blocked('Không tự commit symlink/submodule.')
      safeContent(name, await readFile(path.join(repo.directory, name), 'utf8'))
    } catch (error) { if (error.code !== 'ENOENT') throw error }
  }
  await git(repo, ['diff', '--check', '--', ...names])
}

export async function assertRepo(repo) {
  if ((await git(repo, ['rev-parse', '--show-toplevel'])).trim() !== repo.directory) throw new Blocked('Repo không khớp allowlist.')
  const state = await snapshot(repo)
  if (!repo.branches.includes(state.branch)) throw new Blocked('Nhánh không thuộc allowlist.')
  if (state.staged) throw new Blocked('Index đã có thay đổi; không gom staged của người dùng.')
  for (const marker of ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'sequencer', 'index.lock']) {
    const location = (await git(repo, ['rev-parse', '--git-path', marker])).trim()
    try { await access(path.resolve(repo.directory, location)) } catch (error) { if (error.code === 'ENOENT') continue; throw error }
    throw new Blocked('Git đang có thao tác dở dang/khóa; cần xử lý thủ công.')
  }
  return state
}

export async function upstream(repo, { fetch = false, signal } = {}) {
  const branch = (await git(repo, ['symbolic-ref', '--quiet', '--short', 'HEAD'])).trim()
  if (!repo.branches.includes(branch)) throw new Blocked('Nhánh không thuộc allowlist.')
  const remote = (await git(repo, ['config', '--get', `branch.${branch}.remote`])).trim()
  const merge = (await git(repo, ['config', '--get', `branch.${branch}.merge`])).trim()
  if (remote !== repo.remote || merge !== `refs/heads/${branch}`) throw new Blocked('Upstream không khớp cấu hình được phép.')
  const fetchUrls = (await git(repo, ['remote', 'get-url', '--all', remote])).trim().split('\n')
  const pushUrls = (await git(repo, ['remote', 'get-url', '--push', '--all', remote])).trim().split('\n')
  if ([...fetchUrls, ...pushUrls].some(url => url !== repo.url) || pushUrls.length !== 1) throw new Blocked('URL remote/push URL không khớp allowlist.')
  if (fetch) await git(repo, ['fetch', '--no-tags', '--no-recurse-submodules', remote, `+${merge}:refs/remotes/${remote}/${branch}`], { signal, timeout: 60_000 })
  const ref = `refs/remotes/${remote}/${branch}`
  await git(repo, ['merge-base', '--is-ancestor', ref, 'HEAD'])
  const count = Number((await git(repo, ['rev-list', '--count', `${ref}..HEAD`])).trim())
  return { branch, remote, merge, ref, count }
}

export async function inspectHistory(repo, ref) {
  const commits = (await git(repo, ['rev-list', `${ref}..HEAD`])).trim().split('\n').filter(Boolean)
  const names = new Set()
  for (const commit of commits) {
    const parents = (await git(repo, ['rev-list', '--parents', '-n', '1', commit])).trim().split(' ')
    if (parents.length !== 2) throw new Blocked('Commit merge/root chưa push cần review thủ công.')
    await git(repo, ['diff', '--check', `${commit}^`, commit])
    for (const name of nul(await git(repo, ['diff-tree', '--no-commit-id', '--name-only', '--no-renames', '-z', '-r', commit]))) {
      names.add(name)
      safeName(name)
      const entry = await git(repo, ['ls-tree', '-z', commit, '--', name])
      if (!entry) continue // File đã xóa.
      if (!/^100(?:644|755) blob /.test(entry)) throw new Blocked('Lịch sử có symlink/submodule; cần review thủ công.')
      safeContent(name, await git(repo, ['show', `${commit}:${name}`]))
    }
  }
  return [...names]
}
