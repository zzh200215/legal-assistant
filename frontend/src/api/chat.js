import http from './http'

export default {
  chat(messages, documentId) {
    return http.post('/chat/', { messages, document_id: documentId })
  },
}
