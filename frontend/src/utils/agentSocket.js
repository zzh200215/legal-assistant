export function agentSocketUrl(locationRef = window.location) {
  const protocol = locationRef.protocol === 'https:' ? 'wss' : 'ws'
  const host = import.meta.env.DEV ? (import.meta.env.VITE_WS_HOST || 'localhost:8001') : locationRef.host
  return `${protocol}://${host}/api/ws/agent`
}

export function openAgentSocket({ payload, onMessage, onError, onClose }) {
  const token = localStorage.getItem('token')
  if (!token) throw new Error('AUTH_TOKEN_MISSING')
  const socket = new WebSocket(agentSocketUrl(), ['json', `bearer.${token}`])
  socket.onopen = () => socket.send(JSON.stringify(payload))
  socket.onmessage = (event) => onMessage(JSON.parse(event.data))
  socket.onerror = onError
  socket.onclose = onClose
  return socket
}
