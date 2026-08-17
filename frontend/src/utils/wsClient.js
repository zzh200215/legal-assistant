// WebSocket 客户端（P1 协议合规，docs/websocket-protocol.md 为实现基准）：
//  - 认证：sec-websocket-protocol ["json", "bearer.<token>"]
//  - welcome 首帧（捕获 resume_token / last_seq）；心跳 ping→pong + 客户端看门狗
//  - seq/ack 确认；断线指数退避重连 + resume 恢复；resync_required 时重建订阅
//  - 离线暂停/恢复；认证失败(1008)不自动重连（交由上层 401 流程）
//  - 发送队列（连接建立时 resume 之后按序补发），背压上限丢弃并告警
// 纯 JS、框架无关：供 Agent 执行与 useAsyncJob 复用，可注入 WebSocketImpl 单测。

export const WS_CLOSE = {
  NORMAL: 1000,
  AUTH_FAILED: 1008,
  OVERLOADED: 1013,
  IDLE_TIMEOUT: 4001,
  RESUME_INVALID: 4002,
  PROTOCOL_ERROR: 4003,
}

const DEFAULT_MAX_RETRIES = 5
const DEFAULT_RETRY_BASE = 1000
const RETRY_MAX = 30000
const WATCHDOG_MS = 45000 // 超过该时长未收到任何服务端消息（含 ping）视为连接失效
const MAX_QUEUE = 100

/**
 * @param {object} options
 * @param {string} options.url WS 端点
 * @param {string[]} [options.channels] 初始订阅通道（chat/agent/jobs/notifications）
 * @param {() => string|null} [options.token] 取 token 函数
 * @param {(event: object) => void} [options.onEvent] 业务事件回调（welcome 等协议事件也透传）
 * @param {(status: string) => void} [options.onStatus] 连接状态回调
 * @param {number} [options.maxRetries] 重连次数上限
 * @param {number} [options.retryBase] 重连基础延迟(ms)，指数退避
 * @param {typeof WebSocket} [options.WebSocketImpl] 测试注入用
 */
export function createWsClient(options) {
  const {
    url,
    channels = [],
    token = () => (typeof localStorage !== 'undefined' ? localStorage.getItem('token') : null),
    onEvent = () => {},
    onStatus = () => {},
    maxRetries = DEFAULT_MAX_RETRIES,
    retryBase = DEFAULT_RETRY_BASE,
    WebSocketImpl = typeof WebSocket !== 'undefined' ? WebSocket : null,
  } = options

  let ws = null
  let resumeToken = null
  let lastSeq = 0
  let ackedSeq = 0
  let retries = 0
  let manualClose = false
  let paused = false
  let watchdogTimer = null
  let reconnectTimer = null
  let queue = []
  let status = 'idle'
  const listeners = new Set()
  listeners.add(onEvent)

  function setStatus(next) {
    if (status === next) return
    status = next
    onStatus(next)
  }

  function stopWatchdog() {
    if (watchdogTimer) {
      clearTimeout(watchdogTimer)
      watchdogTimer = null
    }
  }

  function startWatchdog() {
    stopWatchdog()
    watchdogTimer = setTimeout(() => {
      if (ws && !manualClose && !paused) {
        try {
          ws.close(4000, 'watchdog timeout')
        } catch {
          // 已关闭则忽略
        }
      }
    }, WATCHDOG_MS)
  }

  function sendRaw(obj) {
    if (ws && ws.readyState === WebSocketImpl.OPEN) {
      ws.send(JSON.stringify(obj))
      return true
    }
    return false
  }

  /** 发送消息；连接未就绪时入队（上限内），背压超限返回 false */
  function send(obj) {
    if (sendRaw(obj)) return true
    if (queue.length < MAX_QUEUE) {
      queue.push(obj)
      return true
    }
    return false
  }

  function flushQueue() {
    while (queue.length && ws && ws.readyState === WebSocketImpl.OPEN) {
      ws.send(JSON.stringify(queue.shift()))
    }
  }

  function handleMessage(event) {
    let data
    try {
      data = JSON.parse(event.data)
    } catch {
      return
    }
    if (data && typeof data.seq === 'number') {
      lastSeq = Math.max(lastSeq, data.seq)
      sendRaw({ type: 'ack', ack_seq: data.seq })
    }
    if (data?.type === 'welcome') {
      if (data.resume_token) resumeToken = data.resume_token
      if (typeof data.last_seq === 'number') lastSeq = Math.max(lastSeq, data.last_seq)
      if (!data.resumed) sendRaw({ type: 'subscribe', channels })
      startWatchdog()
      emitEvent(data)
      return
    }
    if (data?.type === 'ping') {
      sendRaw({ type: 'pong' })
      startWatchdog()
      emitEvent(data)
      return
    }
    if (data?.type === 'resync_required') {
      resumeToken = null
      ackedSeq = 0
      sendRaw({ type: 'subscribe', channels })
      startWatchdog()
      emitEvent(data)
      return
    }
    startWatchdog()
    emitEvent(data)
  }

  function emitEvent(data) {
    listeners.forEach((fn) => {
      try {
        fn(data)
      } catch {
        // 单个监听器异常不影响其他监听器
      }
    })
  }

  /** 追加事件监听（供 useAsyncJob 等共享连接方使用） */
  function addListener(fn) {
    listeners.add(fn)
  }

  function removeListener(fn) {
    listeners.delete(fn)
  }

  function scheduleReconnect() {
    if (manualClose || paused) return
    if (retries >= maxRetries) {
      setStatus('error')
      return
    }
    retries += 1
    const delay = Math.min(retryBase * 2 ** (retries - 1), RETRY_MAX)
    reconnectTimer = setTimeout(() => connect(), delay)
  }

  function connect() {
    if (manualClose || paused) return
    if (ws && (ws.readyState === WebSocketImpl.OPEN || ws.readyState === WebSocketImpl.CONNECTING)) return
    const tok = typeof token === 'function' ? token() : token
    if (!tok) {
      setStatus('error')
      return
    }
    setStatus(retries > 0 ? 'reconnecting' : 'connecting')
    let socket
    try {
      socket = new WebSocketImpl(url, ['json', `bearer.${tok}`])
    } catch {
      scheduleReconnect()
      return
    }
    ws = socket
    socket.onopen = () => {
      retries = 0
      setStatus('open')
      if (resumeToken) {
        sendRaw({ type: 'resume', resume_token: resumeToken, ack_seq: ackedSeq })
      } else {
        sendRaw({ type: 'subscribe', channels })
      }
      flushQueue()
      startWatchdog()
    }
    socket.onmessage = (event) => handleMessage(event)
    socket.onclose = (event) => {
      stopWatchdog()
      if (manualClose || event.code === WS_CLOSE.NORMAL) {
        ws = null
        setStatus('closed')
        return
      }
      if (event.code === WS_CLOSE.AUTH_FAILED) {
        // 认证失败不自动重连，交由上层 401 流程处理
        ws = null
        setStatus('error')
        return
      }
      ws = null
      setStatus('reconnecting')
      scheduleReconnect()
    }
    socket.onerror = () => {
      // 错误后必然触发 onclose
    }
  }

  function pause() {
    paused = true
    stopWatchdog()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      try {
        ws.close(WS_CLOSE.NORMAL, 'paused')
      } catch {
        // 忽略
      }
      ws = null
    }
    setStatus('closed')
  }

  function resume() {
    paused = false
    connect()
  }

  function close(code = WS_CLOSE.NORMAL) {
    manualClose = true
    stopWatchdog()
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      try {
        ws.close(code)
      } catch {
        // 忽略
      }
      ws = null
    }
    queue = []
    setStatus('closed')
  }

  /** 订阅/退订通道（合并去重，避免覆盖其他调用方的订阅） */
  function subscribe(nextChannels) {
    nextChannels.forEach((c) => {
      if (!channels.includes(c)) channels.push(c)
    })
    send({ type: 'subscribe', channels: nextChannels })
  }

  /** 取消 job / agent_run（与 REST 取消同权限，幂等） */
  function cancel(kind, id) {
    return send({ type: 'cancel', kind, id })
  }

  return {
    connect,
    close,
    pause,
    resume,
    send,
    subscribe,
    cancel,
    addListener,
    removeListener,
    getStatus: () => status,
    get resumeToken() {
      return resumeToken
    },
    get lastSeq() {
      return lastSeq
    },
  }
}
