import http from './http'

export default {
  getFeatureFlags() { return http.get('/legal/features') },
  searchArticles(q) { return http.get(`/legal/article-search?q=${encodeURIComponent(q)}`) },
  listDeveloperApps(orgId) { return http.get(`/legal/orgs/${orgId}/apps`) },
  createDeveloperApp(orgId, payload) { return http.post(`/legal/orgs/${orgId}/apps`, payload) },
  rotateDeveloperKey(orgId, appId) { return http.post(`/legal/orgs/${orgId}/apps/${appId}/keys/rotate`) },
  getOperationsSummary(orgId) { return http.get(`/legal/orgs/${orgId}/operations/summary`) },
  // Contract lifecycle
  createLegalContract(orgId, payload) { return http.post(`/legal/orgs/${orgId}/contracts`, payload) },
  listLegalContracts(orgId, caseId) {
    return http.get(`/legal/orgs/${orgId}/contracts${caseId ? `?case_id=${caseId}` : ''}`)
  },
  listLegalContractVersions(contractId) { return http.get(`/legal/contracts/${contractId}/versions`) },
  createLegalContractVersion(contractId, payload) { return http.post(`/legal/contracts/${contractId}/versions`, payload) },
  confirmLegalContractVersion(contractId, versionId) { return http.post(`/legal/contracts/${contractId}/versions/${versionId}/confirm`) },
  diffLegalContractVersions(contractId, baseVersion, targetVersion) {
    return http.get(`/legal/contracts/${contractId}/diff?base_version=${baseVersion}&target_version=${targetVersion}`)
  },
  listContractMilestones(contractId) { return http.get(`/legal/contracts/${contractId}/milestones`) },
  listSignRequests(contractId) { return http.get(`/legal/contracts/${contractId}/sign-requests`) },
  createSignRequest(contractId, payload) { return http.post(`/legal/contracts/${contractId}/sign-requests`, payload) },
  sendSignRequest(requestId) {
    return http.post(`/legal/sign-requests/${requestId}/send`)
  },

  // Time entries
  createTimeEntry(orgId, caseId, payload) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/time-entries`, payload)
  },
  patchTimeEntry(entryId, payload) {
    return http.patch(`/legal/time-entries/${entryId}`, payload)
  },
  listTimeEntries(orgId, caseId, page, pageSize) {
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/time-entries?page=${page}&page_size=${pageSize}`)
  },

  // Billing rules
  createBillingRule(orgId, payload) {
    return http.post(`/legal/orgs/${orgId}/billing-rules`, payload)
  },
  listBillingRules(orgId, caseId) {
    return http.get(`/legal/orgs/${orgId}/billing-rules?case_id=${caseId}`)
  },

  // Invoices
  createInvoice(orgId, payload) {
    return http.post(`/legal/orgs/${orgId}/invoices`, payload)
  },
  listInvoices(orgId, caseId, status, page, pageSize) {
    const params = [`case_id=${caseId}`, `page=${page}`, `page_size=${pageSize}`]
    if (status) params.push(`status=${status}`)
    return http.get(`/legal/orgs/${orgId}/invoices?${params.join('&')}`)
  },
  sendInvoice(invoiceId) {
    return http.post(`/legal/invoices/${invoiceId}/send`)
  },
  voidInvoice(invoiceId, reason) {
    return http.post(`/legal/invoices/${invoiceId}/void`, null, { params: { reason } })
  },
  recordPayment(invoiceId, payload) {
    return http.post(`/legal/invoices/${invoiceId}/payments`, payload)
  },
  createRefund(invoiceId, payload) {
    return http.post(`/legal/invoices/${invoiceId}/refunds`, payload)
  },
  listPayments(invoiceId) {
    return http.get(`/legal/invoices/${invoiceId}/payments`)
  },
  createCollectionReminder(invoiceId, note) {
    return http.post(`/legal/invoices/${invoiceId}/collection-reminders`, undefined, { params: note ? { note } : undefined })
  },

  listOrgMembers(orgId) {
    return http.get(`/legal/orgs/${orgId}/members`)
  },

  // Deadlines
  createDeadline(orgId, caseId, payload) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/deadlines`, payload)
  },
  patchDeadline(deadlineId, payload) {
    return http.patch(`/legal/deadlines/${deadlineId}`, payload)
  },
  listDeadlines(orgId, caseId, status, page, pageSize) {
    const params = [`page=${page}`, `page_size=${pageSize}`]
    if (status) params.push(`status=${status}`)
    return http.get(`/legal/orgs/${orgId}/cases/${caseId}/deadlines?${params.join('&')}`)
  },
  deadlineToCalendar(deadlineId) {
    return http.post(`/legal/deadlines/${deadlineId}/calendar-suggestion`)
  },

  // Portal authentication and client-content access
  portalSendOtp(token) {
    return http.post(`/legal/portal/${token}/send-otp`)
  },
  portalVerifyOtp(token, otp) {
    return http.post(`/legal/portal/${token}/verify`, null, { params: { otp } })
  },
  portalGetContent(token, sessionToken) {
    return http.get(`/legal/portal/${token}/content`, { headers: { 'X-Portal-Session': sessionToken } })
  },
  portalDownloadDocument(token, documentId, sessionToken) {
    return http.get(`/legal/portal/${token}/documents/${documentId}/download`, {
      headers: { 'X-Portal-Session': sessionToken },
      responseType: 'blob',
    })
  },

  // Case members
  addCaseMember(orgId, caseId, payload) {
    return http.post(`/legal/orgs/${orgId}/cases/${caseId}/members`, payload)
  },
}
