import http from './http'

export default {
  getNotifications(config = {}) { return http.get('/developer/notifications/me', config) },
  markNotificationRead(id) { return http.post(`/developer/notifications/${id}/read`) },
  markAllNotificationsRead() { return http.post('/developer/notifications/read-all') },
  getNotificationPreferences(config = {}) {
    return http.get('/developer/notification-preferences/me', config)
  },
  updateNotificationPreference(eventType, payload) {
    return http.put('/developer/notification-preferences/me', payload, {
      params: { event_type: eventType },
    })
  },
  getNotificationLeave() { return http.get('/developer/notification-leave/me') },
  updateNotificationLeave(payload) {
    return http.put('/developer/notification-leave/me', payload)
  },
  clearNotificationLeave() { return http.delete('/developer/notification-leave/me') },
}
