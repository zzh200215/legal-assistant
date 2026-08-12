import agent from './agent'
import analytics from './analytics'
import auth from './auth'
import document from './document'
import legal from './legal'
import legalWorkspace from './legalWorkspace'
import memory from './memory'
import notifications from './notifications'
import org from './org'
import subscription from './subscription'
import task from './task'

const api = {
  ...agent,
  ...analytics,
  ...auth,
  ...document,
  ...legal,
  ...legalWorkspace,
  ...memory,
  ...notifications,
  ...org,
  ...subscription,
  ...task,
}

export { agent, analytics, auth, document, legal, legalWorkspace, memory, notifications, org, subscription, task }
export default api
