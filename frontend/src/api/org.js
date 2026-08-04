import http from './http'

export default {
  listOrganizations() { return http.get('/org/organizations') },
  createOrganization(data) { return http.post('/org/organizations', data) },
  listDepartments(params) { return http.get('/org/departments', { params }) },
  createDepartment(data) { return http.post('/org/departments', data) },
  assignUserOrg(userId, data) { return http.post(`/org/users/${userId}/assign`, data) },
}
