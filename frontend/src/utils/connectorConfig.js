export function parseConnectorConfig(raw) {
  if (!raw) return {}
  const parsed = JSON.parse(raw)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new TypeError('Connector configuration must be an object')
  return parsed
}

export function enterpriseCredentialKey(type, authMode = 'access_token') {
  const microsoft = type === 'ms_graph_onedrive' || type === 'ms_graph_sharepoint'
  return microsoft ? (authMode === 'oauth_client_secret' ? 'client_secret' : 'access_token') : 'bearer_token'
}
