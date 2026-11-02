import { Plugin } from '@opencode/plugin'
import { randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import config from './config.mjs'
import { Coordinator, atomic, settings } from './coordinator.mjs'

export const isMutation = tool => /(?:^|[._-])(?:patch|edit|write|shell|bash|execute)$/.test(tool)
export const isBackground = event => event.input?.background === true
  || event.result?.metadata?.background === true
  || (/(?:shell|bash)$/.test(event.tool) && /(?:^|\s)(?:nohup|setsid|disown)\b|(?:^|[^&])&(?!&)/.test(event.input?.command ?? ''))

// Export riêng để test hooks/events mà không cần model/provider hoặc server thật.
export async function setup(ctx, policy = config) {
  if (!/^2\.2\./.test(ctx.app.version)) throw new Error('Garblo AutoGit chỉ được kiểm tra với OpenCode 2.2.x.')
  const directory = ctx.location.directory
  if (![policy.workspace, ...policy.repos.map(repo => repo.directory)].includes(directory)) return
  const controller = new AbortController()
  const timers = new Map()
  const promptIDs = new Map()
  const registrations = []
  const notices = []
  const coordinator = new Coordinator(policy, {
    authorize: async (action, repo, turn) => {
      if (!turn?.sessionID) throw new Error('Automation cần session để kiểm tra quyền.')
      const session = await ctx.session.get({ sessionID: turn.sessionID })
      if (session.agent !== 'build' || (!turn.manual && session.outcome !== 'succeeded')) throw new Error('Session chưa hoàn tất thành công bằng agent build; không chạy automation.')
      await ctx.permission.assert({
        sessionID: turn.sessionID, agent: 'build',
        action: `garblo.autogit.${action}`, resources: [repo.directory],
        metadata: { repo: repo.id, remote: repo.remote, branches: repo.branches },
      })
    },
    notify: entry => {
      // Không tạo prompt/model request mới vì sẽ gây vòng lặp auto-commit.
      notices.push(`${entry.repo}: ${entry.status}${entry.reason ? ` — ${entry.reason}` : ''}`)
      if (notices.length > 20) notices.shift()
    },
  })
  const clear = sessionID => {
    clearTimeout(timers.get(sessionID))
    timers.delete(sessionID)
    coordinator.cancel(sessionID)
  }
  registrations.push(await ctx.session.hook('prompt', event => {
    clear(event.sessionID)
    promptIDs.set(event.sessionID, event.messageID)
  }))
  registrations.push(await ctx.session.hook('context', async event => {
    const session = await ctx.session.get({ sessionID: event.sessionID })
    if (session.location.directory !== directory) return
    if (event.agent !== 'build') { await coordinator.abandon(event.sessionID); return }
    await coordinator.begin({ sessionID: event.sessionID, turnID: promptIDs.get(event.sessionID) ?? randomUUID(), agent: event.agent, directory, parentID: session.parentID })
    event.system.push({ type: 'text', text: 'AutoGit đang quản lý commit/push sau lượt: không tự chạy git add/commit/push nếu người dùng không yêu cầu trực tiếp. Dùng tool ghi đồng bộ; tool chạy nền hoặc ghi chồng lấn sẽ chặn automation. Chỉ agent build được tự commit. Kết quả gần nhất xem /autogit-status.' })
    if (notices.length) event.system.push({ type: 'text', text: `AutoGit gần nhất:\n${notices.splice(0).join('\n')}` })
  }))
  registrations.push(await ctx.tool.hook('execute.before', async event => {
    if (isMutation(event.tool)) await coordinator.beforeTool(event.sessionID, event.id)
  }))
  registrations.push(await ctx.tool.hook('execute.after', async event => {
    if (isMutation(event.tool)) await coordinator.afterTool(event.sessionID, event.id, { background: isBackground(event) })
  }))
  registrations.push(await ctx.command.transform(editor => {
    editor.add({
      name: 'autogit-status', description: 'Xem mode và kết quả AutoGit FE/BE; không chạy model.',
      execute: async ({ sessionID }) => {
        const entries = []
        for (const repo of policy.repos) {
          try { entries.push(JSON.parse(await readFile(path.join(policy.stateDirectory, `last-${repo.id}.json`), 'utf8'))) } catch (error) { if (error.code !== 'ENOENT') throw error }
        }
        await ctx.session.synthetic({ sessionID, text: `AutoGit ${(await settings(policy)).mode}; ngưỡng ${policy.threshold}/repo.\n${entries.map(e => `${e.repo}: ${e.status}${e.reason ? ` — ${e.reason}` : ''}`).join('\n') || 'Chưa có lượt được theo dõi.'}` })
      },
    })
    editor.add({
      name: 'autogit-mode', description: 'Đổi AutoGit: off, dry-run hoặc on.',
      execute: async ({ sessionID, prompt }) => {
        const mode = prompt.text.trim()
        if (!['off', 'dry-run', 'on'].includes(mode)) throw new Error('Dùng /autogit-mode off|dry-run|on')
        const session = await ctx.session.get({ sessionID })
        await ctx.permission.assert({ sessionID, agent: session.agent, action: 'garblo.autogit.configure', resources: [policy.workspace] })
        await atomic(path.join(policy.stateDirectory, 'settings.json'), { mode })
        await ctx.session.synthetic({ sessionID, text: `AutoGit: ${mode}` })
      },
    })
    editor.add({
      name: 'autogit-retry', description: 'Kiểm tra/push lại commit local: FE hoặc BE. Không stage file.',
      execute: async ({ sessionID, prompt }) => {
        const session = await ctx.session.get({ sessionID })
        if (session.agent !== 'build') throw new Error('Chuyển sang agent build để chạy lại AutoGit.')
        const repo = policy.repos.find(repo => repo.id === prompt.text.trim())
        if (!repo) throw new Error('Dùng /autogit-retry FE|BE')
        const result = await coordinator.retry(repo, { sessionID, manual: true })
        await ctx.session.synthetic({ sessionID, text: `AutoGit ${repo.id}: ${result.status}` })
      },
    })
  }))
  const stream = (async () => {
    for await (const event of ctx.event.subscribe({ signal: controller.signal, types: ['session.execution.succeeded', 'session.execution.failed', 'session.execution.interrupted', 'session.inbox.enqueued'] })) {
      const sessionID = event.data.sessionID
      if (event.location && event.location.directory !== directory) continue
      if (!event.location && !coordinator.turns.has(sessionID) && !coordinator.pending.has(sessionID)) continue
      if (event.type === 'session.execution.succeeded') {
        clear(sessionID)
        timers.set(sessionID, setTimeout(() => {
          timers.delete(sessionID)
          void coordinator.finish(sessionID).catch(() => notices.push('AutoGit bị lỗi; xem status trước khi thử lại.'))
        }, policy.settleMilliseconds ?? 1500))
      } else if (event.type === 'session.inbox.enqueued') clear(sessionID)
      else { clear(sessionID); await coordinator.abandon(sessionID) }
    }
  })()
  // Rejection được xử lý ngay, không tạo unhandled rejection khi unload/abort.
  const stopped = stream.catch(() => notices.push('Luồng sự kiện AutoGit đã dừng; chưa có automation cho các lượt tiếp theo.'))
  return async () => {
    controller.abort()
    for (const sessionID of timers.keys()) clear(sessionID)
    await coordinator.close()
    for (const registration of registrations) await registration.dispose()
    await stopped
  }
}

export default Plugin.define({ id: 'garblo-autogit', setup })
