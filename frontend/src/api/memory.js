import http from './http'

export default {
  listMemoryPreferences() {
    return http.get('/memory/preferences')
  },
  saveMemoryPreference(payload) {
    return http.post('/memory/preferences', payload)
  },
  deleteMemoryPreference(id) {
    return http.delete(`/memory/preferences/${id}`)
  },
  getSessionMemory(sessionId) {
    return http.get(`/memory/sessions/${sessionId}`)
  },
}
