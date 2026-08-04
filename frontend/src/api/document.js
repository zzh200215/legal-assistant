import http from './http'

export default {
  listDocuments(params) { return http.get('/documents/', { params }) },
  listKnowledgeBases() { return http.get('/documents/knowledge-bases') },
  createKnowledgeBase(payload) { return http.post('/documents/knowledge-bases', payload) },
  uploadDocument(file, asyncMode = false, options = {}) {
    const form = new FormData()
    form.append('file', file)
    Object.entries(options || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      form.append(key, value)
    })
    return http.post(`/documents/upload?async_mode=${asyncMode}`, form)
  },
  batchUploadDocuments(files, asyncMode = false, options = {}) {
    const form = new FormData()
    ;(files || []).forEach((file) => form.append('files', file))
    Object.entries(options || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      form.append(key, value)
    })
    return http.post(`/documents/batch-upload?async_mode=${asyncMode}`, form)
  },
  getDocument(id) { return http.get(`/documents/${id}`) },
  updateDocumentDownloadPolicy(id, payload) { return http.patch(`/documents/${id}/download-policy`, payload) },
  downloadDocument(id) { return http.get(`/documents/${id}/download`, { responseType: 'blob' }) },
  listDocumentVersions(id) { return http.get(`/documents/${id}/versions`) },
  retryDocumentParse(id) { return http.post(`/documents/${id}/retry-parse`) },
  analyzeDocument(id, maxLen = 500, asyncMode = false) {
    return http.post(`/documents/${id}/analyze`, { max_length: maxLen, async_mode: asyncMode })
  },
  compareDocuments(documentIds, maxLen = 500) {
    return http.post('/documents/compare', { document_ids: documentIds, max_length: maxLen })
  },
  createConflictSuggestions(payload) { return http.post('/document-conflicts/suggestions', payload) },
  listConflictCases(params) { return http.get('/document-conflicts/', { params }) },
  confirmConflictTask(id, payload = {}) { return http.post(`/document-conflicts/${id}/confirm-task`, payload) },
  updateConflictStatus(id, payload) { return http.patch(`/document-conflicts/${id}/status`, payload) },
  summarizeDocument(id, maxLen = 500, asyncMode = false) {
    return http.post(`/documents/${id}/summarize`, { max_length: maxLen, async_mode: asyncMode })
  },
  askDocument(id, question) {
    return http.post(`/documents/${id}/ask`, { question })
  },
  extractRisks(id) { return http.post(`/documents/${id}/extract-risks`) },
  extractTodos(id) { return http.post(`/documents/${id}/extract-todos`) },
  extractClauses(id) { return http.post(`/documents/${id}/extract-clauses`) },
  createTasksFromDocument(id) {
    return http.post(`/documents/${id}/create-tasks`)
  },
  listDocumentParseJobs(id, params) {
    return http.get(`/documents/${id}/parse-jobs`, { params })
  },
  listDocumentQaRecords(id, params) {
    return http.get(`/documents/${id}/qa-records`, { params })
  },
  listDocumentQaReplays(id, params) {
    return http.get(`/documents/${id}/qa-replays`, { params })
  },
  submitQaFeedback(qaRecordId, payload) {
    return http.post(`/documents/qa-records/${qaRecordId}/feedback`, payload)
  },
  getDocumentTaskStatus(taskId) {
    return http.get(`/documents/task/${taskId}/status`)
  },
}
