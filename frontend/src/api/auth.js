import http from './http'

export default {
  register(data) { return http.post('/auth/register', data) },
  login(data) { return http.post('/auth/login', data) },
  forgotPassword(data) { return http.post('/auth/forgot-password', data) },
  resetPassword(data) { return http.post('/auth/reset-password', data) },
  getMe() { return http.get('/auth/me') },
  listUsers() { return http.get('/auth/users') },
}
