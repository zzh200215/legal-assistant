import http from './http'

export default {
  listTasks(status, params = {}) {
    const nextParams = { ...params }
    if (status) nextParams.status = status
    return http.get('/tasks/', { params: nextParams })
  },
  getTask(id) { return http.get(`/tasks/${id}`) },
  createTask(data) { return http.post('/tasks/', data) },
  updateTask(id, data) { return http.put(`/tasks/${id}`, data) },
  decomposeTask(id) { return http.post(`/tasks/${id}/decompose`) },
  getSubTasks(id, params) { return http.get(`/tasks/${id}/sub-tasks`, { params }) },
  listTaskComments(id) { return http.get(`/tasks/${id}/comments`) },
  addTaskComment(id, data) { return http.post(`/tasks/${id}/comments`, data) },
  listTaskLogs(id) { return http.get(`/tasks/${id}/logs`) },
  extractTasksFromDoc(docId) {
    return http.post('/tasks/extract-from-document', { document_id: docId })
  },
  extractTasksFromChat(message) {
    return http.post('/tasks/extract-from-chat', { message })
  },
}
