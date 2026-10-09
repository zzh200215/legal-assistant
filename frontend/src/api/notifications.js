import http from './http'

export default {
  getNotifications(config = {}) { return http.get('/developer/notifications/me', config) },
  markNotificationRead(id) { return http.post(`/developer/notifications/${id}/read`) },
  markAllNotificationsRead() { return http.post('/developer/notifications/read-all') },
}
