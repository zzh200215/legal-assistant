import http, { idempotencyHeaders } from './http'

// The legal workspace owns these workflows: source management, consultation,
// contract review, drafting, review queue, and the case portal shown on its page.
// 写操作统一支持 options.idempotencyKey（useMutation 注入）；GET 支持 config.signal。

export default {
  getLegalOverview(config = {}) {
    return http.get('/legal/overview', config)
  },
  listCases(orgId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/cases`, config)
  },
  createCase(orgId, payload, options = {}) {
    return http.post(`/legal/orgs/${orgId}/cases`, payload, { headers: idempotencyHeaders(options) })
  },
  listCaseItems(orgId, caseId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/items`, config)
  },
  listLegalSources(config = {}) {
    return http.get('/legal/sources', config)
  },
  importLegalSources(file, options = {}) {
    const form = new FormData()
    form.append('file', file)
    return http.post('/legal/sources/import', form, { headers: idempotencyHeaders(options) })
  },
  updateSourceStatus(sourceId, status, options = {}) {
    return http.patch(`/legal/sources/${sourceId}/status`, { status }, { headers: idempotencyHeaders(options) })
  },
  createSource(payload, options = {}) {
    return http.post('/legal/sources', payload, { headers: idempotencyHeaders(options) })
  },
  updateSource(sourceId, payload, options = {}) {
    return http.put(`/legal/sources/${sourceId}`, payload, { headers: idempotencyHeaders(options) })
  },
  deleteSource(sourceId, options = {}) {
    return http.delete(`/legal/sources/${sourceId}`, { headers: idempotencyHeaders(options) })
  },
  testRetrieval(question, options = {}) {
    return http.post('/legal/sources/retrieval-test', { question }, { headers: idempotencyHeaders(options) })
  },

  createLegalConsultation(payload, options = {}) {
    return http.post('/legal/consultations', payload, { headers: idempotencyHeaders(options) })
  },
  listLegalConsultations(config = {}) {
    return http.get('/legal/consultations', config)
  },
  followupConsultation(id, question, options = {}) {
    return http.post(`/legal/consultations/${id}/followup`, { question }, { headers: idempotencyHeaders(options) })
  },

  createContractReview(payload, options = {}) {
    return http.post('/legal/contract-reviews', payload, { headers: idempotencyHeaders(options) })
  },
  listContractReviews(config = {}) {
    return http.get('/legal/contract-reviews', config)
  },
  uploadContractReview(file, title, caseId, options = {}) {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    if (caseId) form.append('case_id', caseId)
    return http.post('/legal/contract-reviews/upload', form, { headers: idempotencyHeaders(options) })
  },
  compareContracts(payload, options = {}) {
    return http.post('/legal/contract-compare', payload, { headers: idempotencyHeaders(options) })
  },
  listContractReviewVersions(id, config = {}) {
    return http.get(`/legal/contract-reviews/${id}/versions`, config)
  },
  resubmitContractReview(id, payload, options = {}) {
    return http.post(`/legal/contract-reviews/${id}/resubmit`, payload, { headers: idempotencyHeaders(options) })
  },

  listLegalTemplates(config = {}) {
    return http.get('/legal/document-templates', config)
  },
  createLegalDraft(payload, options = {}) {
    return http.post('/legal/drafts', payload, { headers: idempotencyHeaders(options) })
  },
  listLegalDrafts(config = {}) {
    return http.get('/legal/drafts', config)
  },
  exportLegalDraftDocx(id) {
    return http.get(`/legal/drafts/${id}/export/docx`, { responseType: 'blob' })
  },
  listDraftVersions(id, config = {}) {
    return http.get(`/legal/drafts/${id}/versions`, config)
  },
  resubmitDraft(id, payload, options = {}) {
    return http.post(`/legal/drafts/${id}/resubmit`, payload, { headers: idempotencyHeaders(options) })
  },

  getMetrics(config = {}) {
    return http.get('/legal/metrics', config)
  },
  listLegalReviewQueue(config = {}) {
    return http.get('/legal/review-queue', config)
  },
  submitLegalReviewAction(targetType, targetId, payload, options = {}) {
    return http.post(`/legal/review-queue/${targetType}/${targetId}/actions`, payload, { headers: idempotencyHeaders(options) })
  },
  getReviewHistory(targetType, targetId, config = {}) {
    return http.get(`/legal/review-queue/${targetType}/${targetId}/history`, config)
  },
  getReviewStats(config = {}) {
    return http.get('/legal/review-stats', config)
  },
  addReviewComment(targetType, targetId, note, options = {}) {
    return http.post(`/legal/review-queue/${targetType}/${targetId}/comments`, { note }, { headers: idempotencyHeaders(options) })
  },

  submitConsultationFeedback(id, payload, options = {}) {
    return http.post(`/legal/consultations/${id}/feedback`, payload, { headers: idempotencyHeaders(options) })
  },
  submitReviewFeedback(id, payload, options = {}) {
    return http.post(`/legal/contract-reviews/${id}/feedback`, payload, { headers: idempotencyHeaders(options) })
  },
  submitDraftFeedback(id, payload, options = {}) {
    return http.post(`/legal/drafts/${id}/feedback`, payload, { headers: idempotencyHeaders(options) })
  },
  getSourceArticles(sourceId, config = {}) {
    return http.get(`/legal/sources/${sourceId}/articles`, config)
  },

  createPortalLink(orgId, caseId, payload, options = {}) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/portal-links`, payload, { headers: idempotencyHeaders(options) })
  },
  listPortalLinks(orgId, caseId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/portal-links`, config)
  },
  revokePortalLink(linkId, options = {}) {
    return http.post(`/legal/portal-links/${linkId}/revoke`, null, { headers: idempotencyHeaders(options) })
  },
  getPortalBranding(orgId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/portal-branding`, config)
  },
  updatePortalBranding(orgId, payload, options = {}) {
    return http.put(`/legal/orgs/${orgId}/portal-branding`, payload, { headers: idempotencyHeaders(options) })
  },
  listCaseMembers(orgId, caseId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/members`, config)
  },
  patchCaseMember(memberId, payload, options = {}) {
    return http.patch(`/legal/case-members/${memberId}`, payload, { headers: idempotencyHeaders(options) })
  },
  createProgressUpdate(orgId, caseId, payload, options = {}) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/progress-updates`, payload, { headers: idempotencyHeaders(options) })
  },
  publishProgressUpdate(updateId, options = {}) {
    return http.post(`/legal/progress-updates/${updateId}/publish`, null, { headers: idempotencyHeaders(options) })
  },
  withdrawProgressUpdate(updateId, options = {}) {
    return http.post(`/legal/progress-updates/${updateId}/withdraw`, null, { headers: idempotencyHeaders(options) })
  },
  listProgressUpdates(orgId, caseId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/progress-updates`, config)
  },
  portalSubmitFeedback(token, payload, session) {
    return http.post(`/legal/portal/${token}/feedback`, payload, {
      headers: session ? { 'X-Portal-Session': session } : {},
    })
  },
  listPortalFeedback(orgId, config = {}) {
    return http.get(`/legal/orgs/${orgId}/portal-feedback`, config)
  },
}
