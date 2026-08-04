import agent from './agent'
import analytics from './analytics'
import auth from './auth'
import chat from './chat'
import connector from './connector'
import document from './document'
import legal from './legal'
import legalWorkspace from './legalWorkspace'
import memory from './memory'
import org from './org'
import subscription from './subscription'
import task from './task'

const api = {
  ...agent,
  ...analytics,
  ...auth,
  ...chat,
  ...connector,
  ...document,
  ...legal,
  ...legalWorkspace,
  ...memory,
  ...org,
  ...subscription,
  ...task,
}

export { agent, analytics, auth, chat, connector, document, legal, legalWorkspace, memory, org, subscription, task }
export default api
