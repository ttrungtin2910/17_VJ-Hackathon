import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises'
import path from 'node:path'
import { setup, isMutation, isBackground } from '../plugin.mjs'
import { install } from '../install.mjs'
import { git, snapshot } from '../git.mjs'

async function mock(t) {
  const workspace = await mkdtemp('/tmp/opencode/garblo-plugin-test-')
  t.after(() => rm(workspace, { recursive: true, force: true }))
  const directory = path.join(workspace, 'repo')
  await mkdir(directory)
  const repo = { id: 'BE', directory, branches: ['main'], remote: 'origin', url: '/not-used-in-dry-run' }
  await git(repo, ['init', '--initial-branch=main'])
  await git(repo, ['config', 'user.name', 'Test'])
  await git(repo, ['config', 'user.email', 'test@example.invalid'])
  await writeFile(path.join(directory, 'README.md'), '# Test\n')
  await git(repo, ['add', 'README.md'])
  await git(repo, ['commit', '-m', 'init'])
  await git(repo, ['remote', 'add', 'origin', repo.url])
  await git(repo, ['config', 'branch.main.remote', 'origin'])
  await git(repo, ['config', 'branch.main.merge', 'refs/heads/main'])
  await git(repo, ['update-ref', 'refs/remotes/origin/main', 'HEAD'])
  const policy = { workspace, stateDirectory: path.join(workspace, '.opencode/autogit'), threshold: 3, settleMilliseconds: 5, repos: [repo] }
  await install(policy)
  const hooks = {}, commands = {}, synthetic = [], permissions = [], queue = []
  let wake = () => {}
  const hook = domain => async (name, callback) => { hooks[`${domain}:${name}`] = callback; return { dispose: async () => {} } }
  const session = { agent: 'build', outcome: 'succeeded', location: { directory: workspace } }
  const ctx = {
    app: { version: '2.2.2' }, location: { directory: workspace },
    session: { hook: hook('session'), get: async () => session, synthetic: async input => { synthetic.push(input) } },
    tool: { hook: hook('tool') }, permission: { assert: async input => { permissions.push(input) } },
    command: { transform: async callback => { callback({ add: command => { commands[command.name] = command } }); return { dispose: async () => {} } } },
    event: { subscribe: ({ signal, types }) => (async function* () {
      signal.addEventListener('abort', () => wake(), { once: true })
      while (!signal.aborted) {
        if (!queue.length) await new Promise(resolve => { wake = resolve })
        while (queue.length) { const event = queue.shift(); if (types.includes(event.type)) yield event }
      }
    })() },
  }
  const close = await setup(ctx, policy)
  t.after(close)
  const send = type => { queue.push({ type, data: { sessionID: 's' } }); wake() } // Event có thể không có location.
  const context = async agent => hooks['session:context']({ sessionID: 's', agent: agent ?? session.agent, system: [] })
  const edit = async () => {
    await hooks['tool:execute.before']({ sessionID: 's', id: 'patch-1', tool: 'patch', input: {} })
    await writeFile(path.join(directory, 'new.md'), '# New\n')
    await hooks['tool:execute.after']({ sessionID: 's', id: 'patch-1', tool: 'patch', input: {}, status: 'completed', result: {} })
  }
  const status = async () => {
    for (let i = 0; i < 100; i++) {
      try { return JSON.parse(await readFile(path.join(policy.stateDirectory, 'last-BE.json'), 'utf8')) } catch (error) { if (error.code !== 'ENOENT') throw error }
      await new Promise(resolve => setTimeout(resolve, 10))
    }
    throw new Error('Không nhận được kết quả event.')
  }
  return { repo, policy, ctx, hooks, commands, synthetic, permissions, send, context, edit, status, session, close }
}

test('plugin V2: hook theo contract → event succeeded không location → dry-run, không tạo prompt', async t => {
  const { repo, context, edit, send, status, commands, synthetic, permissions } = await mock(t)
  const initial = (await snapshot(repo)).head
  await context()
  await edit()
  send('session.execution.succeeded')
  assert.equal((await status()).status, 'dry-run')
  assert.equal((await snapshot(repo)).head, initial)
  assert.equal(permissions.length, 0)
  await commands['autogit-status'].execute({ sessionID: 's' })
  assert.match(synthetic[0].text, /dry-run/)
})

test('plugin bỏ qua Plan và event failed/interrupted; unload giải phóng khóa', async t => {
  const { context, edit, send, repo, close } = await mock(t)
  const initial = (await snapshot(repo)).head
  await context('plan')
  await context()
  await edit()
  send('session.execution.failed')
  await close()
  assert.equal((await snapshot(repo)).head, initial)
  assert.equal((await snapshot(repo)).staged, false)
})

test('installer không ghi đè config và mode yêu cầu quyền riêng', async t => {
  const { policy, commands, permissions, session } = await mock(t)
  await assert.rejects(install(policy), /Đã có/)
  await assert.rejects(commands['autogit-mode'].execute({ sessionID: 's', prompt: { text: 'invalid' } }), /Dùng/)
  await commands['autogit-mode'].execute({ sessionID: 's', prompt: { text: 'off' } })
  assert.equal(permissions[0].action, 'garblo.autogit.configure')
  session.agent = 'plan'
  await assert.rejects(commands['autogit-retry'].execute({ sessionID: 's', prompt: { text: 'BE' } }), /build/)
})

test('chỉ tool ghi được nhận quyền sở hữu, không quy read/glob thành mutation', () => {
  for (const name of ['patch', 'functions.shell', 'functions_shell', 'execute', 'write', 'bash']) assert.equal(isMutation(name), true)
  for (const name of ['read', 'glob', 'grep', 'multi_tool_use.parallel']) assert.equal(isMutation(name), false)
  assert.equal(isBackground({ tool: 'shell', input: { command: 'npm test && npm run check' } }), false)
  for (const input of [{ command: 'npm run dev &' }, { command: 'nohup server' }, { background: true }]) assert.equal(isBackground({ tool: 'shell', input }), true)
})
