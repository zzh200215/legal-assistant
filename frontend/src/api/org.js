import http, { idempotencyHeaders } from './http'

export default {
  listOrganizations(config = {}) {
    return http.get('/org/organizations', config)
  },
  createOrganization(data, options = {}) {
    return http.post('/org/organizations', data, { headers: idempotencyHeaders(options) })
  },
  // 组织更新支持 If-Match 版本冲突（后端 409 CONCURRENT_UPDATE_CONFLICT）；
  // 调用方需先从 GET /org/organizations/{id} 的 ETag 或响应体 version 捕获版本号。
  updateOrganization(id, data, options = {}) {
    return http.put(`/org/organizations/${id}`, data, { headers: idempotencyHeaders(options) })
  },
  listDepartments(params = {}, config = {}) {
    return http.get('/org/departments', { params, ...config })
  },
  createDepartment(data, options = {}) {
    return http.post('/org/departments', data, { headers: idempotencyHeaders(options) })
  },
  assignUserOrg(userId, data, options = {}) {
    return http.post(`/org/users/${userId}/assign`, data, { headers: idempotencyHeaders(options) })
  },
}
