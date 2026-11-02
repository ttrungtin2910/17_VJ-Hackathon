import { readFile } from 'node:fs/promises'
import path from 'node:path'
import config from './config.mjs'
import { atomic, settings, Coordinator } from './coordinator.mjs'
import { snapshot, upstream } from './git.mjs'
import { install } from './install.mjs'

const [command = 'status', arg] = process.argv.slice(2)
try {
  if (command === 'install') {
    console.log(`Đã cài ${await install(config)}; mode=dry-run. Không thay đổi Git.`)
  } else if (command === 'mode') {
    if (!['off', 'dry-run', 'on'].includes(arg)) throw new Error('Dùng: mode off|dry-run|on')
    await atomic(path.join(config.stateDirectory, 'settings.json'), { mode: arg })
    console.log(`AutoGit: ${arg}`)
  } else if (command === 'status') {
    console.log(`AutoGit: ${(await settings(config)).mode}; ngưỡng ${config.threshold} commit/repo`)
    for (const repo of config.repos) {
      try {
        const state = await snapshot(repo)
        const remote = await upstream(repo)
        let last = 'chưa có'
        try { const entry = JSON.parse(await readFile(path.join(config.stateDirectory, `last-${repo.id}.json`), 'utf8')); last = `${entry.status}${entry.reason ? `: ${entry.reason}` : ''}` } catch (error) { if (error.code !== 'ENOENT') throw error }
        console.log(`${repo.id}: ${state.branch}, ahead ${remote.count} (cache local), ${Object.keys(state.files).length} file thay đổi; gần nhất: ${last}`)
      } catch (error) { console.log(`${repo.id}: bị chặn: ${error.message}`) }
    }
  } else if (command === 'retry') {
    const repo = config.repos.find(repo => repo.id === arg)
    if (!repo) throw new Error('Dùng: retry FE|BE (chỉ kiểm tra/push commit đã có, không stage file).')
    const coordinator = new Coordinator(config)
    console.log(JSON.stringify(await coordinator.retry(repo), null, 2))
  } else throw new Error('Dùng: install | status | mode off|dry-run|on | retry FE|BE')
} catch (error) {
  console.error(`AutoGit: ${error.message}`)
  process.exitCode = 1
}
