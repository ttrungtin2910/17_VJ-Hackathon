import { readFile, access, mkdtemp, rm } from 'node:fs/promises'
import path from 'node:path'
import { Blocked, run } from './git.mjs'

export const docsOnly = names => names.length > 0 && names.every(name => /\.(md|rst|txt)$/i.test(name))
export const infra = names => names.some(name => /(^|\/)(Dockerfile[^/]*|compose[^/]*\.ya?ml|package(-lock)?\.json|pyproject\.toml|alembic\.ini)$/.test(name) || /(^|\/)migrations\//.test(name) || /^deploy\//.test(name))

export async function checkLinks(repo, names) {
  for (const name of names.filter(name => name.endsWith('.md'))) {
    let text
    try { text = await readFile(path.join(repo.directory, name), 'utf8') } catch (error) { if (error.code === 'ENOENT') continue; throw error }
    // Bỏ code fence và inline code để không coi ví dụ cú pháp Markdown là link thật.
    text = text.replace(/^```[^\n]*\n[\s\S]*?^```/gm, '').replace(/`[^`\n]+`/g, '')
    for (const match of text.matchAll(/\]\((?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\)/g)) {
      const url = match[1] ?? match[2]
      if (/^(?:[a-z][a-z0-9+.-]*:|#|\/)/i.test(url)) continue
      let target
      try { target = decodeURIComponent(url.split('#')[0].split('?')[0]) } catch { throw new Blocked('Link tài liệu không hợp lệ.') }
      if (!target) continue
      try { await access(path.resolve(repo.directory, path.dirname(name), target)) } catch { throw new Blocked(`Link nội bộ không tồn tại trong ${name}.`) }
    }
  }
}

async function python(repo) {
  const local = path.join(repo.directory, '.venv/bin/python')
  try { await access(local); return local } catch { return 'python3' }
}

export async function checks(repo, names, { signal, onCheck = () => {} } = {}) {
  await checkLinks(repo, names)
  if (docsOnly(names)) return
  const command = async (label, executable, args, extra = {}) => {
    onCheck(label)
    return run(repo.directory, executable, args, { signal, ...extra })
  }
  if (repo.checks) {
    // Chỉ dùng bởi cấu hình test hoặc profile riêng đã được người dùng duyệt.
    for (const [executable, ...args] of repo.checks) await command(executable, executable, args)
    return
  }
  const toolingOnly = repo.id === 'BE' && names.every(name => name.startsWith('tools/opencode-autogit/') || /\.(md|rst|txt)$/.test(name))
  if (names.some(name => name.startsWith('tools/opencode-autogit/'))) {
    await command('automation: cú pháp', 'npm', ['--prefix', 'tools/opencode-autogit', 'run', 'check'])
    await command('automation: test', 'npm', ['--prefix', 'tools/opencode-autogit', 'test'])
  }
  if (toolingOnly) return
  if (repo.id === 'FE') {
    await command('Node >=24', 'node', ['-e', 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'])
    for (const script of ['typecheck', 'lint', 'test', 'build', 'test:e2e']) await command(`FE: ${script}`, 'npm', ['run', script])
    if (infra(names)) {
      if (!process.env.GARBLO_E2E_BASE_URL) throw new Blocked('Thay đổi hạ tầng FE cần GARBLO_E2E_BASE_URL của stack test riêng.')
      await command('FE: real E2E', 'npm', ['exec', '--', 'playwright', 'test', '--config=playwright.real.config.ts'])
    }
  } else if (repo.id === 'BE') {
    const py = await python(repo)
    await command('Python >=3.12', py, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'])
    await command('BE: unit', py, ['-m', 'pytest', '-q', '-m', 'not integration'])
    await command('BE: lint', py, ['-m', 'ruff', 'check', 'src', 'tests', 'migrations'])
    await command('BE: format', py, ['-m', 'ruff', 'format', '--check', 'src', 'tests', 'migrations'])
    await command('BE: script local', 'bash', ['local-wsl/tests/test-scripts.sh'])
    if (infra(names) || names.some(name => name.startsWith('local-wsl/'))) {
      // Chỉ validate cấu hình với sample/test env; không up/down/rebuild stack.
      await command('BE: compose mặc định', 'docker', ['compose', '--env-file', '.env.example', '-f', 'compose.yaml', 'config', '--quiet'], { env: { GARBLO_ENV_FILE: path.join(repo.directory, '.env.example') } })
      for (const override of [[], ['-f', 'local-wsl/deploy/compose.test.yml'], ['-f', 'local-wsl/deploy/compose.controlled.yml']]) {
        const sample = path.join(repo.directory, 'local-wsl/tests/env.test')
        await command('BE: compose local/test', 'docker', ['compose', '--project-directory', 'local-wsl', '--env-file', sample, '-f', 'local-wsl/deploy/compose.local.yml', ...override, 'config', '--quiet'], { env: { GARBLO_ENV_FILE: sample } })
      }
      for (const name of ['GARBLO_TEST_DATABASE_URL', 'GARBLO_TEST_S3_ENDPOINT_URL', 'GARBLO_TEST_S3_ACCESS_KEY', 'GARBLO_TEST_S3_SECRET_KEY', 'GARBLO_TEST_API_URL']) {
        if (!process.env[name]) throw new Blocked(`Thay đổi hạ tầng/migration cần ${name} của stack test riêng.`)
      }
      const temp = await mkdtemp(path.join(process.env.OPENCODE_TMP_DIR ?? '/tmp/opencode', 'garblo-autogit-junit-'))
      try {
        const report = path.join(temp, 'integration.xml')
        await command('BE: integration', py, ['-m', 'pytest', '-q', '-m', 'integration', `--junitxml=${report}`])
        const xml = await readFile(report, 'utf8')
        if (!/tests="[1-9]\d*"/.test(xml) || /skipped="[1-9]/.test(xml)) throw new Blocked('Integration không chạy đủ hoặc có skip; chặn push.')
      } finally { await rm(temp, { recursive: true, force: true }) }
    }
  } else throw new Blocked('Chưa có profile kiểm tra cho repo.')
}
