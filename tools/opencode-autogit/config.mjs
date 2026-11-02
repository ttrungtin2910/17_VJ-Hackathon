import path from 'node:path'

const workspace = path.resolve(import.meta.dirname, '../../..')
export default {
  workspace,
  stateDirectory: path.join(workspace, '.opencode/autogit'),
  threshold: 3,
  repos: [
    { id: 'FE', directory: path.join(workspace, 'garblo-frontend'), remote: 'origin', url: 'git@github.com:Thinhjp/Garblo-Frontend.git', branches: ['main'] },
    { id: 'BE', directory: path.join(workspace, 'garblo-backend'), remote: 'origin', url: 'git@github.com:ttrungtin2910/17_VJ-Hackathon.git', branches: ['rebuild/openai-fpt-ai-factory'] },
  ],
}
