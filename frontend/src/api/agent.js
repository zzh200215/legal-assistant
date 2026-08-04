import http from './http'

export default {
  getAgentRegistry() { return http.get('/agent/registry') },
  previewAgentPlan(goal, maxSteps = 5) {
    return http.post('/agent/preview', { goal, max_steps: maxSteps })
  },
  runAgent(goal, maxSteps = 5) {
    return http.post('/agent/run', { goal, max_steps: maxSteps })
  },
  listAgentRuns(params) {
    return http.get('/agent/runs', { params })
  },
  getAgentRun(id) {
    return http.get(`/agent/runs/${id}`)
  },
  getAgentLogs(id) {
    return http.get(`/agent/runs/${id}/logs`)
  },
  cancelAgentRun(id, reason = '') { return http.post(`/agent/runs/${id}/cancel`, { reason }) },
  retryAgentRun(id) { return http.post(`/agent/runs/${id}/retry`) },
  getAgentMetrics(days = 30) { return http.get('/agent/metrics', { params: { days } }) },
  listApprovals(params) {
    return http.get('/agent/approvals', { params })
  },
  decideApproval(id, payload) {
    return http.post(`/agent/approvals/${id}/decision`, payload)
  },
  resumeApproval(id, payload = {}) {
    return http.post(`/agent/approvals/${id}/resume`, payload)
  },
}
