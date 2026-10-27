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
  funnelSummary(days = 30) {
    return http.get('/analytics/funnel/summary', { params: { days } })
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
  consultationEvidenceStats(days = 30) {
    return http.get('/analytics/consultations/evidence-stats', { params: { days } })
  },
  consultationEvidenceIssues(days = 30, params = {}) {
    return http.get('/analytics/consultations/evidence-issues', { params: { days, ...params } })
  },
  resolveConsultationEvidenceIssue(consultationId, note = null) {
    return http.post(`/analytics/consultations/evidence-issues/${consultationId}/resolve`, { note })
  },
  reopenConsultationEvidenceIssue(consultationId) {
    return http.post(`/analytics/consultations/evidence-issues/${consultationId}/reopen`)
  },
  exportConsultationCorpusGaps(days = 30) {
    return http.get('/analytics/consultations/evidence-issues/export', {
      params: { days }, responseType: 'blob',
    })
  },
  notifyConsultationEvidenceIssues(days = 30, params = {}) {
    return http.post('/analytics/consultations/evidence-issues/notify', null, { params: { days, ...params } })
  },
  retrievalIndexStats() {
    return http.get('/analytics/retrieval-index/stats')
  },
  workflowOverview(days = 7) {
    return http.get('/analytics/workflows/overview', { params: { days } })
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
  outboxOverview(params) {
    return http.get('/analytics/outbox/overview', { params })
  },
  outboxHistory(params) {
    return http.get('/analytics/outbox/history', { params })
  },
  retryOutboxDeadLetter(kind, itemId) {
    return http.post(`/analytics/outbox/dead-letters/${kind}/${itemId}/retry`)
  },
  notificationTemplates(params) {
    return http.get('/admin/notification-templates/', { params })
  },
  previewNotificationTemplate(data) {
    return http.post('/admin/notification-templates/preview', data)
  },
  createNotificationTemplate(data) {
    return http.post('/admin/notification-templates/', data)
  },
  activateNotificationTemplate(id) {
    return http.post(`/admin/notification-templates/${id}/activate`)
  },
}
