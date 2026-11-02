import { mkdir, writeFile, access } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { atomic } from './coordinator.mjs'

export async function install(config, pluginDirectory = import.meta.dirname) {
  const directory = path.join(config.workspace, '.opencode')
  // Không tự merge JSONC, không ghi đè cấu hình có thể thuộc người dùng.
  for (const file of ['opencode.json', 'opencode.jsonc', '.opencode/opencode.json', '.opencode/opencode.jsonc']) {
    try { await access(path.join(config.workspace, file)) } catch (error) { if (error.code === 'ENOENT') continue; throw error }
    throw new Error(`Đã có ${file}; hãy merge plugin và quyền có phạm vi thủ công.`)
  }
  const permissions = config.repos.flatMap(repo => ['commit', 'fetch', 'check', 'push'].map(action => ({ action: `garblo.autogit.${action}`, resource: repo.directory, effect: 'allow' })))
  permissions.push({ action: 'garblo.autogit.configure', resource: config.workspace, effect: 'allow' })
  await mkdir(directory, { recursive: true })
  const file = path.join(directory, 'opencode.json')
  await writeFile(file, JSON.stringify({
    $schema: 'https://opencode.ai/config.json',
    plugins: [pathToFileURL(pluginDirectory).href],
    permissions,
  }, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
  await atomic(path.join(config.stateDirectory, 'settings.json'), { mode: 'dry-run' })
  return file
}
