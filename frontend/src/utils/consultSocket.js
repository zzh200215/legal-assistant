// 法律咨询的 WS 流式通道（docs/websocket-protocol.md §2 `{"type":"consult"}`）。
//
// 与 REST `POST /api/legal/consultations` 同一条业务链路，区别只在过程可见：
// 服务端每完成一步推一帧 `consult_step`，最后推 `consult_done`（完整结果）。
// 面板优先走这里（能实时看到检索/生成/抽取/核验），连不上或首帧前失败时回退 REST。
//
// 失败语义：拒绝值带 `code` 与 `streamed` —— `streamed === false` 表示还没有任何步骤帧，
// 调用方可以安全地改用 REST 重发（不会重复消耗配额）；`streamed === true` 表示咨询已经在跑，
// 此时不应重发（会重复扣配额），交由调用方提示用户重试。

import { createWsClient } from './wsClient.js'

export const CONSULT_STREAM_TIMEOUT_MS = 120000
/** 连接建立的上限：超时即认为实时通道不可用，交调用方回退 REST */
export const CONSULT_CONNECT_TIMEOUT_MS = 4000

export function consultSocketUrl(locationRef = window.location) {
  const protocol = locationRef.protocol === 'https:' ? 'wss' : 'ws'
  // Vite 注入 import.meta.env；纯 Node（单测）下没有该对象，此时按同源处理
  const dev = Boolean(import.meta.env && import.meta.env.DEV)
  const host = dev ? (import.meta.env.VITE_WS_HOST || 'localhost:8001') : locationRef.host
  return `${protocol}://${host}/api/ws/chat`
}

/**
 * @param {object} options
 * @param {object} options.payload 咨询请求体（content 由调用方放进 payload.content）
 * @param {(step: object) => void} [options.onStep] 步骤回调（服务端 trace 的单个步骤）
 * @param {number} [options.timeoutMs]
 * @param {Location} [options.locationRef] 测试注入
 * @param {typeof WebSocket} [options.WebSocketImpl] 测试注入
 * @returns {Promise<object>} 解析为 consult_done 的 consultation
 */
export function streamConsultation(options = {}) {
  const {
    payload,
    onStep = () => {},
    timeoutMs = CONSULT_STREAM_TIMEOUT_MS,
    connectTimeoutMs = CONSULT_CONNECT_TIMEOUT_MS,
    locationRef = typeof window !== 'undefined' ? window.location : undefined,
    WebSocketImpl,
    token,
  } = options

  return new Promise((resolve, reject) => {
    let settled = false
    let streamed = false
    let opened = false
    let timer = null
    let connectTimer = null
    let client = null

    const cleanup = () => {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      if (connectTimer) {
        clearTimeout(connectTimer)
        connectTimer = null
      }
      if (client) {
        try {
          client.close()
        } catch {
          // 已关闭则忽略
        }
      }
    }

    const fail = (code, message) => {
      if (settled) return
      settled = true
      cleanup()
      const error = new Error(message || code)
      error.code = code
      error.streamed = streamed
      reject(error)
    }

    const succeed = (consultation) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(consultation)
    }

    try {
      client = createWsClient({
        url: consultSocketUrl(locationRef),
        channels: ['chat'],
        WebSocketImpl,
        token,
        // 单次请求：连不上就交给调用方回退 REST，不在这一层反复重连
        // （重连会拖长「提问→有反应」的等待，而 REST 回退更快也更可靠）
        maxRetries: 0,
        onStatus: (status) => {
          // 认证失败/连接失败：此时还没有任何步骤，调用方可以安全回退 REST
          if (status === 'error' && !streamed) fail('WS_UNAVAILABLE', '实时通道不可用')
        },
        onEvent: (data) => {
          if (!data || typeof data !== 'object') return
          // 收到任何服务端帧都说明连接已经建立：连接超时不再适用
          if (!opened) {
            opened = true
            if (connectTimer) {
              clearTimeout(connectTimer)
              connectTimer = null
            }
          }
          if (data.type === 'welcome') return
          if (data.type === 'consult_step') {
            streamed = true
            onStep(data)
            return
          }
          if (data.type === 'consult_done') {
            succeed(data.consultation || {})
            return
          }
          if (data.type === 'error') {
            fail(data.code || 'WS_ERROR', data.message)
          }
        },
      })
    } catch {
      fail('WS_UNAVAILABLE', '实时通道不可用')
      return
    }

    // 连接迟迟建不起来（既不 open 也不 close）：按不可用处理，让调用方回退 REST
    connectTimer = setTimeout(() => {
      if (!opened) fail('WS_UNAVAILABLE', '实时通道不可用')
    }, connectTimeoutMs)
    timer = setTimeout(() => fail('WS_TIMEOUT', '实时通道超时'), timeoutMs)
    client.connect()
    if (!client.send({ type: 'consult', ...payload })) {
      fail('WS_UNAVAILABLE', '实时通道不可用')
    }
  })
}
