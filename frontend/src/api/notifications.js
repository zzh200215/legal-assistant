import http from './http'

export default {
  getNotifications() { return http.get('/developer/notifications/me') },
  markNotificationRead(id) { return http.post(`/developer/notifications/${id}/read`) },
  markAllNotificationsRead() { return http.post('/developer/notifications/read-all') },
}
