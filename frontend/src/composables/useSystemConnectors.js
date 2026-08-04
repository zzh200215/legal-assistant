import { computed, ref } from 'vue'
import { enterpriseCredentialKey, parseConnectorConfig } from '../utils/connectorConfig'

const emptyConnector = () => ({ connector_type: 'drive', name: '', config_json: '', secret: '', graph_auth_mode: 'access_token' })
const emptyCredentialDialog = () => ({ visible: false, connector: null, secret: '', graph_auth_mode: 'access_token' })

export function useSystemConnectors({ client, message, isAdmin, focusedJobId, initialConnectorId, refreshTasks, refreshAlerts }) {
  const connectorLoading = ref(false)
  const connectors = ref([])
  const connectorJobs = ref([])
  const newConnector = ref(emptyConnector())
  const enterpriseCredentialDialog = ref(emptyCredentialDialog())
  const connectorJobFilter = ref({ connector_id: initialConnectorId || null, status: null })
  const enterpriseConnectorTypes = new Set(['ms_graph_onedrive', 'ms_graph_sharepoint', 'erp_rest', 'crm_rest'])
  const isEnterpriseConnector = (type) => enterpriseConnectorTypes.has(type)
  const isMicrosoftConnector = (type) => type === 'ms_graph_onedrive' || type === 'ms_graph_sharepoint'
  const enterpriseSecretPlaceholder = (type, graphAuthMode = 'access_token') => {
    if (isMicrosoftConnector(type)) return graphAuthMode === 'oauth_client_secret' ? 'Azure 应用 client_secret' : 'Microsoft Graph access token'
    return 'Bearer Token 或 API Key'
  }
  const enterpriseConfigHint = (type, graphAuthMode = 'access_token') => {
    if (isMicrosoftConnector(type) && graphAuthMode === 'oauth_client_secret') return '请在配置中填写 tenant_id、client_id；创建后点击“OAuth 授权”完成管理员同意。'
    return '仅系统管理员可配置企业数据源；令牌将加密保存，不会再次显示。'
  }
  const connectorConfigPlaceholder = computed(() => {
    const type = newConnector.value.connector_type
    const oauthFields = newConnector.value.graph_auth_mode === 'oauth_client_secret' ? ',"tenant_id":"Azure 租户 ID","client_id":"应用 ID"' : ''
    if (type === 'ms_graph_onedrive') return `OneDrive 示例：{"drive_id":"可选，默认当前用户网盘","max_files":50,"permission_scope":"org"${oauthFields}}`
    if (type === 'ms_graph_sharepoint') return `SharePoint 示例：{"site_id":"contoso.sharepoint.com,site-id,web-id","max_files":50,"permission_scope":"org"${oauthFields}}`
    if (type === 'erp_rest' || type === 'crm_rest') return 'REST 示例：{"endpoint":"https://api.example.com/v1","resource_path":"/customers","items_path":"data.items","title_field":"name","content_fields":["name","status"],"cursor_field":"updated_at"}'
    return '配置 JSON；OA 审批示例：{"endpoint":"https://oa.example.com/api/approvals"}。令牌请在创建后通过凭据轮换保存。'
  })
  const fetchConnectorData = async () => {
    connectorLoading.value = true
    try {
      const params = {}
      if (connectorJobFilter.value.connector_id) params.connector_id = connectorJobFilter.value.connector_id
      if (connectorJobFilter.value.status) params.status = connectorJobFilter.value.status
      const [connectorRes, jobRes] = await Promise.all([client.listConnectors(), client.listConnectorSyncJobs(params)])
      connectors.value = connectorRes.data || []
      const rows = jobRes.data || []
      if (focusedJobId.value) rows.sort((a, b) => (a.id === focusedJobId.value ? -1 : b.id === focusedJobId.value ? 1 : 0))
      connectorJobs.value = rows
    } catch (error) {
      connectors.value = []; connectorJobs.value = []
      message.error(error.response?.data?.detail || '获取连接器失败')
    } finally { connectorLoading.value = false }
  }
  const connectorJobRowClassName = ({ row }) => focusedJobId.value && row?.id === focusedJobId.value ? 'focused-connector-job-row' : ''
  const createConnector = async () => {
    if (!newConnector.value.name) return
    connectorLoading.value = true
    try {
      if (isEnterpriseConnector(newConnector.value.connector_type)) {
        let config
        try { config = parseConnectorConfig(newConnector.value.config_json) } catch { message.error('企业连接器配置必须是合法 JSON'); return }
        const key = enterpriseCredentialKey(newConnector.value.connector_type, newConnector.value.graph_auth_mode)
        await client.createEnterpriseConnector({ connector_type: newConnector.value.connector_type, name: newConnector.value.name, config, credentials: { [key]: newConnector.value.secret } })
      } else {
        await client.createConnector({ connector_type: newConnector.value.connector_type, name: newConnector.value.name, config_json: newConnector.value.config_json })
      }
      newConnector.value = emptyConnector(); message.success('连接器已创建'); await fetchConnectorData()
    } catch (error) { message.error(error.response?.data?.detail || '连接器创建失败') } finally { connectorLoading.value = false }
  }
  const openEnterpriseCredentialDialog = (row) => { enterpriseCredentialDialog.value = { ...emptyCredentialDialog(), visible: true, connector: row } }
  const saveEnterpriseCredentials = async () => {
    const row = enterpriseCredentialDialog.value.connector
    if (!row?.id || !enterpriseCredentialDialog.value.secret) return
    connectorLoading.value = true
    try {
      const key = enterpriseCredentialKey(row.connector_type, enterpriseCredentialDialog.value.graph_auth_mode)
      await client.updateEnterpriseConnectorCredentials(row.id, { credentials: { [key]: enterpriseCredentialDialog.value.secret } })
      enterpriseCredentialDialog.value = emptyCredentialDialog(); message.success('企业连接器凭据已更新')
    } catch (error) { message.error(error.response?.data?.detail || '更新凭据失败') } finally { connectorLoading.value = false }
  }
  const startMicrosoftOAuth = async (row) => {
    const popup = window.open('', 'microsoftConnectorOAuth', 'width=560,height=720'); connectorLoading.value = true
    try {
      const redirectUri = new URL('/api/connectors/microsoft/callback', window.location.origin).toString()
      const { data } = await client.startMicrosoftOAuth(row.id, { redirect_uri: redirectUri })
      if (popup) popup.location.href = data.authorize_url; else window.location.href = data.authorize_url
      message.info('请在 Microsoft 页面完成管理员授权，完成后返回此页面刷新连接器状态。')
    } catch (error) { if (popup) popup.close(); message.error(error.response?.data?.detail || '无法发起 Microsoft 授权') } finally { connectorLoading.value = false }
  }
  const syncConnector = async (row) => {
    connectorLoading.value = true
    try { await client.syncConnector(row.id); message.success('同步任务已提交'); await fetchConnectorData(); await refreshTasks(); await refreshAlerts() }
    catch (error) { message.error(error.response?.data?.detail || '连接器同步失败') } finally { connectorLoading.value = false }
  }
  return { connectorLoading, connectors, connectorJobs, newConnector, enterpriseCredentialDialog, connectorJobFilter, isEnterpriseConnector, isMicrosoftConnector, enterpriseSecretPlaceholder, enterpriseConfigHint, connectorConfigPlaceholder, fetchConnectorData, connectorJobRowClassName, createConnector, openEnterpriseCredentialDialog, saveEnterpriseCredentials, startMicrosoftOAuth, syncConnector }
}
