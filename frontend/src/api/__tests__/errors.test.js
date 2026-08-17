import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeError, ErrorKind } from '../errors.js'

test('业务错误：稳定错误码映射 + request_id/trace_id 提取', () => {
  const err = normalizeError({
    response: {
      status: 404,
      data: {
        success: false,
        message: '文档不存在',
        error: { code: 'DOCUMENT_NOT_FOUND', detail: '文档不存在或无权访问' },
        detail: '文档不存在',
        request_id: 'req-1',
        trace_id: 'trace-1',
      },
    },
  })
  assert.equal(err.kind, ErrorKind.BUSINESS)
  assert.equal(err.code, 'DOCUMENT_NOT_FOUND')
  assert.equal(err.requestId, 'req-1')
  assert.equal(err.traceId, 'trace-1')
  assert.ok(err.message.includes('文档'))
})

test('409 冲突识别', () => {
  const err = normalizeError({ response: { status: 409, data: { error: { code: 'CONCURRENT_UPDATE_CONFLICT' } } } })
  assert.equal(err.kind, ErrorKind.CONFLICT)
  assert.equal(err.code, 'CONCURRENT_UPDATE_CONFLICT')
})

test('401 / 403 区分', () => {
  const unauthorized = normalizeError({ response: { status: 401, data: { error: { code: 'INVALID_CREDENTIALS' } } } })
  assert.equal(unauthorized.kind, ErrorKind.UNAUTHORIZED)
  const forbidden = normalizeError({ response: { status: 403, data: { error: { code: 'ADMIN_REQUIRED' } } } })
  assert.equal(forbidden.kind, ErrorKind.FORBIDDEN)
  assert.equal(forbidden.code, 'ADMIN_REQUIRED')
})

test('网络错误（无响应）识别', () => {
  const err = normalizeError({ response: undefined, code: 'ECONNRESET' })
  assert.equal(err.kind, ErrorKind.NETWORK)
})

test('取消请求识别', () => {
  const err = normalizeError({ code: 'ERR_CANCELED' })
  assert.equal(err.kind, ErrorKind.CANCELLED)
})

test('服务端 500 隐藏内部细节', () => {
  const err = normalizeError({ response: { status: 500, data: { error: { code: 'INTERNAL_SERVER_ERROR', detail: '服务器内部错误' } } } })
  assert.equal(err.kind, ErrorKind.SERVER)
  assert.equal(err.message, '服务器内部错误，请稍后重试')
})

test('同一次调用结果被缓存（幂等）', () => {
  const raw = { response: { status: 429, data: { error: { code: 'RATE_LIMIT' } } } }
  const a = normalizeError(raw)
  const b = normalizeError(raw)
  assert.equal(a, b)
})
