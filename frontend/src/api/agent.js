import http, { idempotencyHeaders } from './http'

export default {
  getAgentRegistry(config = {}) {
    return http.get('/agent/registry', config)
  },
  previewAgentPlan(goal, maxSteps = 5, options = {}) {
    return http.post('/agent/preview', { goal, max_steps: maxSteps }, { headers: idempotencyHeaders(options) })
  },
  runAgent(goal, maxSteps = 5, options = {}) {
    return http.post('/agent/run', { goal, max_steps: maxSteps }, { headers: idempotencyHeaders(options) })
  },
  listAgentRuns(params = {}, config = {}) {
    return http.get('/agent/runs', { params, ...config })
  },
  getAgentRun(id, config = {}) {
    return http.get(`/agent/runs/${id}`, config)
  },
  getAgentLogs(id, config = {}) {
    return http.get(`/agent/runs/${id}/logs`, config)
  },
  cancelAgentRun(id, reason = '', options = {}) {
    return http.post(`/agent/runs/${id}/cancel`, { reason }, { headers: idempotencyHeaders(options) })
  },
  retryAgentRun(id, options = {}) {
    return http.post(`/agent/runs/${id}/retry`, null, { headers: idempotencyHeaders(options) })
  },
  getAgentMetrics(days = 30, config = {}) {
    return http.get('/agent/metrics', { params: { days }, ...config })
  },
  listApprovals(params = {}, config = {}) {
    return http.get('/agent/approvals', { params, ...config })
  },
  decideApproval(id, payload, options = {}) {
    return http.post(`/agent/approvals/${id}/decision`, payload, { headers: idempotencyHeaders(options) })
  },
  resumeApproval(id, payload = {}, options = {}) {
    return http.post(`/agent/approvals/${id}/resume`, payload, { headers: idempotencyHeaders(options) })
  },
}
