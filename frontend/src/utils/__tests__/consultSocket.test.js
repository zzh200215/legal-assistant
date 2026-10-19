import { test } from 'node:test'
import assert from 'node:assert/strict'

import { consultSocketUrl, streamConsultation } from '../consultSocket.js'

// 假 WebSocket：只实现 wsClient 用到的那部分（OPEN 常量、send/close、四个回调）。
class FakeWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3
  static instances = []

  constructor(url, protocols) {
    this.url = url
    this.protocols = protocols
    this.readyState = FakeWebSocket.CONNECTING
    this.sent = []
    this.closed = null
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  emit(payload) {
    this.onmessage?.({ data: JSON.stringify(payload) })
  }

  send(data) {
    this.sent.push(JSON.parse(data))
  }

  close(code = 1000) {
    this.closed = code
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code })
  }

  fail(code) {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code })
  }
}

function reset() {
  FakeWebSocket.instances = []
}

function start(options = {}) {
  reset()
  const steps = []
  const promise = streamConsultation({
    payload: { content: '公司没提前通知就解除合同', case_id: 3 },
    onStep: (step) => steps.push(step),
    WebSocketImpl: FakeWebSocket,
    token: () => 'fake-token',
    locationRef: { protocol: 'http:', host: 'localhost:5173' },
    ...options,
  })
  return { promise, steps, socket: () => FakeWebSocket.instances[0] }
}

test('consultSocketUrl: DEV 默认指向本地后端，https 下用 wss', () => {
  const url = consultSocketUrl({ protocol: 'http:', host: 'localhost:5173' })
  assert.match(url, /^ws:\/\/.+\/api\/ws\/chat$/)
  const secure = consultSocketUrl({ protocol: 'https:', host: 'law.example.com' })
  assert.match(secure, /^wss:\/\/.+\/api\/ws\/chat$/)
})

test('streamConsultation: 连接后发出 consult 消息，逐步回调并解析最终结果', async () => {
  const { promise, steps, socket } = start()
  socket().open()

  assert.deepEqual(socket().sent[0], {
    type: 'subscribe',
    channels: ['chat'],
  })
  assert.deepEqual(socket().sent.at(-1), {
    type: 'consult',
    content: '公司没提前通知就解除合同',
    case_id: 3,
  })

  socket().emit({ type: 'consult_step', id: 'retrieve', label: '检索法源', status: 'success', duration_ms: 12 })
  socket().emit({ type: 'consult_step', id: 'generate', label: '生成建议', status: 'success', duration_ms: 900 })
  socket().emit({ type: 'consult_done', consultation: { id: 77, question: 'q' } })

  const result = await promise
  assert.equal(result.id, 77)
  assert.deepEqual(steps.map((s) => s.id), ['retrieve', 'generate'])
  assert.equal(socket().closed, 1000, '拿到结果后应关闭连接')
})

test('streamConsultation: 业务错误帧带稳定错误码，且标记已开跑（streamed=true）', async () => {
  const { promise, socket } = start()
  socket().open()
  socket().emit({ type: 'consult_step', id: 'retrieve', label: '检索法源', status: 'success' })
  socket().emit({ type: 'error', code: 'QUOTA_EXCEEDED', message: '本月咨询配额已用完' })

  await assert.rejects(promise, (error) => {
    assert.equal(error.code, 'QUOTA_EXCEEDED')
    assert.equal(error.streamed, true, '已经收到步骤帧 → 调用方不应回退 REST 重发')
    assert.match(error.message, /配额/)
    return true
  })
})

test('streamConsultation: 认证失败且尚无步骤时标记 streamed=false（可安全回退 REST）', async () => {
  const { promise, socket } = start()
  socket().open()
  socket().fail(1008)

  await assert.rejects(promise, (error) => {
    assert.equal(error.code, 'WS_UNAVAILABLE')
    assert.equal(error.streamed, false)
    return true
  })
})

test('streamConsultation: 超时按 WS_TIMEOUT 拒绝，不伪造结果', async () => {
  const { promise, socket } = start({ timeoutMs: 5 })
  socket().open()
  await assert.rejects(promise, (error) => {
    assert.equal(error.code, 'WS_TIMEOUT')
    return true
  })
})

test('streamConsultation: 连接迟迟建不起来时按不可用处理（可回退 REST）', async () => {
  const { promise } = start({ connectTimeoutMs: 5, timeoutMs: 500 })
  await assert.rejects(promise, (error) => {
    assert.equal(error.code, 'WS_UNAVAILABLE')
    assert.equal(error.streamed, false)
    return true
  })
})
