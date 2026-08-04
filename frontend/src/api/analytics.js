import http from './http'

export default {
  healthCheck() {
    return http.get('/health')
  },
  myTokenStats(days = 30) {
    return http.get('/analytics/tokens/my-stats', { params: { days } })
  },
  globalTokenStats(days = 30) {
    return http.get('/analytics/tokens/global-stats', { params: { days } })
  },
  dashboardFunnel(days = 30) {
    return http.get('/admin/funnel', { params: { days } })
  },
  dashboardRetention(days = 90) {
    return http.get('/admin/retention', { params: { days } })
  },
  dashboardNorthStar(weeks = 12) {
    return http.get('/admin/north-star', { params: { weeks } })
  },
  experimentOverview(days = 30) {
    return http.get('/analytics/experiments/overview', { params: { days } })
  },
  llmCallStats(params) {
    return http.get('/analytics/llm-calls/stats', { params })
  },
  llmBillingStats(params) {
    return http.get('/analytics/llm-billing/stats', { params })
  },
  llmPricing() {
    return http.get('/analytics/llm-pricing')
  },
  listLlmCalls(params) {
    return http.get('/analytics/llm-calls', { params })
  },
  listOplogs(params) {
    return http.get('/analytics/oplogs', { params })
  },
  oplogStats(days = 30) {
    return http.get('/analytics/oplogs/stats', { params: { days } })
  },
  listAlerts(params) {
    return http.get('/analytics/alerts', { params })
  },
  alertStats(params) {
    return http.get('/analytics/alerts/stats', { params })
  },
  listTaskRuns(params) {
    return http.get('/analytics/task-runs', { params })
  },
  retryTaskRun(data) {
    return http.post('/analytics/task-runs/retry', data)
  },
  listFeedback(params) {
    return http.get('/analytics/feedback', { params })
  },
  listQaReplays(params) {
    return http.get('/analytics/qa-replays', { params })
  },
  feedbackStats(params) {
    return http.get('/analytics/feedback/stats', { params })
  },
  toolHealth(params) {
    return http.get('/analytics/tool-health', { params })
  },
  exportFeedbackEvalBundle(data) {
    return http.post('/analytics/feedback/export-eval-bundle', data)
  },
  resolveFeedback(qaRecordId, data) {
    return http.post(`/analytics/feedback/${qaRecordId}/resolve`, data)
  },
}
