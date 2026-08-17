import http, { idempotencyHeaders } from './http'

// 文档 API：写操作统一支持 options.idempotencyKey（useMutation 注入）与 options.ifMatch，
// GET 支持 config.signal（查询层取消）。禁止在页面散落裸 http 调用。

export default {
  listDocuments(params = {}, config = {}) {
    return http.get('/documents/', { params, ...config })
  },
  listKnowledgeBases(config = {}) {
    return http.get('/documents/knowledge-bases', config)
  },
  createKnowledgeBase(payload, options = {}) {
    return http.post('/documents/knowledge-bases', payload, { headers: idempotencyHeaders(options) })
  },
  uploadDocument(file, asyncMode = false, formOptions = {}, options = {}) {
    const form = new FormData()
    form.append('file', file)
    Object.entries(formOptions || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      form.append(key, value)
    })
    return http.post(`/documents/upload?async_mode=${asyncMode}`, form, { headers: idempotencyHeaders(options) })
  },
  batchUploadDocuments(files, asyncMode = false, formOptions = {}, options = {}) {
    const form = new FormData()
    ;(files || []).forEach((file) => form.append('files', file))
    Object.entries(formOptions || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === '') return
      form.append(key, value)
    })
    return http.post(`/documents/batch-upload?async_mode=${asyncMode}`, form, { headers: idempotencyHeaders(options) })
  },
  getDocument(id, config = {}) {
    return http.get(`/documents/${id}`, config)
  },
  updateDocumentDownloadPolicy(id, payload, options = {}) {
    return http.patch(`/documents/${id}/download-policy`, payload, { headers: idempotencyHeaders(options) })
  },
  downloadDocument(id) {
    return http.get(`/documents/${id}/download`, { responseType: 'blob' })
  },
  listDocumentVersions(id, config = {}) {
    return http.get(`/documents/${id}/versions`, config)
  },
  retryDocumentParse(id, options = {}) {
    return http.post(`/documents/${id}/retry-parse`, null, { headers: idempotencyHeaders(options) })
  },
  analyzeDocument(id, maxLen = 500, asyncMode = false, options = {}) {
    return http.post(`/documents/${id}/analyze`, { max_length: maxLen, async_mode: asyncMode }, { headers: idempotencyHeaders(options) })
  },
  compareDocuments(documentIds, maxLen = 500, options = {}) {
    return http.post('/documents/compare', { document_ids: documentIds, max_length: maxLen }, { headers: idempotencyHeaders(options) })
  },
  createConflictSuggestions(payload, options = {}) {
    return http.post('/document-conflicts/suggestions', payload, { headers: idempotencyHeaders(options) })
  },
  listConflictCases(params = {}, config = {}) {
    return http.get('/document-conflicts/', { params, ...config })
  },
  confirmConflictTask(id, payload = {}, options = {}) {
    return http.post(`/document-conflicts/${id}/confirm-task`, payload, { headers: idempotencyHeaders(options) })
  },
  updateConflictStatus(id, payload, options = {}) {
    return http.patch(`/document-conflicts/${id}/status`, payload, { headers: idempotencyHeaders(options) })
  },
  summarizeDocument(id, maxLen = 500, asyncMode = false, options = {}) {
    return http.post(`/documents/${id}/summarize`, { max_length: maxLen, async_mode: asyncMode }, { headers: idempotencyHeaders(options) })
  },
  askDocument(id, question, options = {}) {
    return http.post(`/documents/${id}/ask`, { question }, { headers: idempotencyHeaders(options) })
  },
  extractRisks(id, options = {}) {
    return http.post(`/documents/${id}/extract-risks`, null, { headers: idempotencyHeaders(options) })
  },
  extractTodos(id, options = {}) {
    return http.post(`/documents/${id}/extract-todos`, null, { headers: idempotencyHeaders(options) })
  },
  extractClauses(id, options = {}) {
    return http.post(`/documents/${id}/extract-clauses`, null, { headers: idempotencyHeaders(options) })
  },
  createTasksFromDocument(id, options = {}) {
    return http.post(`/documents/${id}/create-tasks`, null, { headers: idempotencyHeaders(options) })
  },
  listDocumentParseJobs(id, config = {}) {
    return http.get(`/documents/${id}/parse-jobs`, config)
  },
  listDocumentQaRecords(id, config = {}) {
    return http.get(`/documents/${id}/qa-records`, config)
  },
  listDocumentQaReplays(id, config = {}) {
    return http.get(`/documents/${id}/qa-replays`, config)
  },
  submitQaFeedback(qaRecordId, payload, options = {}) {
    return http.post(`/documents/qa-records/${qaRecordId}/feedback`, payload, { headers: idempotencyHeaders(options) })
  },
  getDocumentTaskStatus(taskId, config = {}) {
    return http.get(`/documents/task/${taskId}/status`, config)
  },
}
