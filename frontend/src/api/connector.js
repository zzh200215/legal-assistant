import http from './http'

export default {
  listConnectors(params) { return http.get('/connectors/', { params }) },
  createConnector(data) { return http.post('/connectors/', data) },
  createEnterpriseConnector(data) { return http.post('/connectors/enterprise', data) },
  updateEnterpriseConnectorCredentials(id, data) { return http.put(`/connectors/${id}/enterprise-credentials`, data) },
  startMicrosoftOAuth(id, data) { return http.post(`/connectors/${id}/microsoft-oauth/start`, data) },
  syncConnector(id, data = { sync_mode: 'manual' }) { return http.post(`/connectors/${id}/sync`, data) },
  rotateConnectorCredentials(id, data) { return http.post(`/connectors/${id}/credentials/rotate`, data) },
  disableConnector(id) { return http.post(`/connectors/${id}/disable`) },
  listConnectorSyncJobs(params) { return http.get('/connectors/sync-jobs', { params }) },
}
