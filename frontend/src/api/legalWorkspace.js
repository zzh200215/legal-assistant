import http from './http'

// The legal workspace owns these workflows: source management, consultation,
// contract review, drafting, review queue, and the case portal shown on its page.
export default {
  getLegalOverview() { return http.get('/legal/overview') },
  listCases(orgId) { return http.get(`/legal/orgs/${orgId}/cases`) },
  createCase(orgId, payload) { return http.post(`/legal/orgs/${orgId}/cases`, payload) },
  listCaseItems(orgId, caseId) { return http.get(`/legal/orgs/${orgId}/cases/${caseId}/items`) },
  listLegalSources() { return http.get('/legal/sources') },
  importLegalSources(file) {
    const form = new FormData()
    form.append('file', file)
    return http.post('/legal/sources/import', form)
  },
  updateSourceStatus(sourceId, status) {
    return http.patch(`/legal/sources/${sourceId}/status`, { status })
  },
  createSource(payload) { return http.post('/legal/sources', payload) },
  updateSource(sourceId, payload) { return http.put(`/legal/sources/${sourceId}`, payload) },
  deleteSource(sourceId) { return http.delete(`/legal/sources/${sourceId}`) },
  testRetrieval(question) {
    return http.post('/legal/sources/retrieval-test', { question })
  },

  createLegalConsultation(payload) { return http.post('/legal/consultations', payload) },
  listLegalConsultations() { return http.get('/legal/consultations') },
  followupConsultation(id, question) { return http.post(`/legal/consultations/${id}/followup`, { question }) },

  createContractReview(payload) { return http.post('/legal/contract-reviews', payload) },
  listContractReviews() { return http.get('/legal/contract-reviews') },
  uploadContractReview(file, title, caseId) {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    if (caseId) form.append('case_id', caseId)
    return http.post('/legal/contract-reviews/upload', form)
  },
  compareContracts(payload) { return http.post('/legal/contract-compare', payload) },
  listContractReviewVersions(id) { return http.get(`/legal/contract-reviews/${id}/versions`) },
  resubmitContractReview(id, payload) { return http.post(`/legal/contract-reviews/${id}/resubmit`, payload) },

  listLegalTemplates() { return http.get('/legal/document-templates') },
  createLegalDraft(payload) { return http.post('/legal/drafts', payload) },
  listLegalDrafts() { return http.get('/legal/drafts') },
  exportLegalDraftDocx(id) { return http.get(`/legal/drafts/${id}/export/docx`, { responseType: 'blob' }) },
  listDraftVersions(id) { return http.get(`/legal/drafts/${id}/versions`) },
  resubmitDraft(id, payload) { return http.post(`/legal/drafts/${id}/resubmit`, payload) },

  getMetrics() { return http.get('/legal/metrics') },
  listLegalReviewQueue() { return http.get('/legal/review-queue') },
  submitLegalReviewAction(targetType, targetId, payload) {
    return http.post(`/legal/review-queue/${targetType}/${targetId}/actions`, payload)
  },
  getReviewHistory(targetType, targetId) {
    return http.get(`/legal/review-queue/${targetType}/${targetId}/history`)
  },
  getReviewStats() { return http.get('/legal/review-stats') },
  addReviewComment(targetType, targetId, note) {
    return http.post(`/legal/review-queue/${targetType}/${targetId}/comments`, { note })
  },

  submitConsultationFeedback(id, payload) {
    return http.post(`/legal/consultations/${id}/feedback`, payload)
  },
  submitReviewFeedback(id, payload) {
    return http.post(`/legal/contract-reviews/${id}/feedback`, payload)
  },
  submitDraftFeedback(id, payload) {
    return http.post(`/legal/drafts/${id}/feedback`, payload)
  },
  getSourceArticles(sourceId) {
    return http.get(`/legal/sources/${sourceId}/articles`)
  },

  createPortalLink(orgId, caseId, payload) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/portal-links`, payload)
  },
  listPortalLinks(orgId, caseId) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/portal-links`)
  },
  revokePortalLink(linkId) {
    return http.post(`/legal/portal-links/${linkId}/revoke`)
  },
  getPortalBranding(orgId) {
    return http.get(`/legal/orgs/${orgId}/portal-branding`)
  },
  updatePortalBranding(orgId, payload) {
    return http.put(`/legal/orgs/${orgId}/portal-branding`, payload)
  },
  listCaseMembers(orgId, caseId) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/members`)
  },
  patchCaseMember(memberId, payload) {
    return http.patch(`/legal/case-members/${memberId}`, payload)
  },
  createProgressUpdate(orgId, caseId, payload) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/progress-updates`, payload)
  },
  publishProgressUpdate(updateId) {
    return http.post(`/legal/progress-updates/${updateId}/publish`)
  },
  withdrawProgressUpdate(updateId) {
    return http.post(`/legal/progress-updates/${updateId}/withdraw`)
  },
  listProgressUpdates(orgId, caseId) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/progress-updates`)
  },
}
