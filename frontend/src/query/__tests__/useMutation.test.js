import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useMutation } from '../useMutation.js'
import { useQuery } from '../useQuery.js'

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('同一逻辑操作复用同一 Idempotency-Key；成功后重置', async () => {
  const scope = effectScope()
  const keys = []
  let mutation
  scope.run(() => {
    mutation = useMutation({
      mutationFn: async (vars, ctx) => {
        keys.push(ctx.idempotencyKey)
        return { done: true }
      },
    })
  })
  await mutation.mutate({ a: 1 })
  const key1 = keys[0]
  assert.ok(typeof key1 === 'string' && key1.length > 0)
  await mutation.mutate({ a: 2 })
  const key2 = keys[1]
  assert.notEqual(key1, key2, '成功后新逻辑操作使用新 key')
  scope.stop()
})

test('进行中连点合并：只发一次请求、同一 key', async () => {
  const scope = effectScope()
  let calls = 0
  let mutation
  scope.run(() => {
    mutation = useMutation({
      mutationFn: async (vars, ctx) => {
        calls += 1
        await sleep(10)
        return ctx.idempotencyKey
      },
    })
  })
  const [r1, r2, r3] = await Promise.all([mutation.mutate({}), mutation.mutate({}), mutation.mutate({})])
  assert.equal(calls, 1)
  assert.equal(r1, r2)
  assert.equal(r2, r3)
  scope.stop()
})

test('网络类失败保留 key（重试复用），业务失败重置 key', async () => {
  const scope = effectScope()
  const keys = []
  let mutation
  scope.run(() => {
    mutation = useMutation({
      mutationFn: async (vars, ctx) => {
        keys.push(ctx.idempotencyKey)
        if (vars.fail === 'network') throw { response: undefined, code: 'ECONNRESET' }
        if (vars.fail === 'business') throw { response: { status: 422, data: { error: { code: 'VALIDATION_ERROR' } } } }
        return { ok: true }
      },
    })
  })
  await mutation.mutate({ fail: 'network' }).catch(() => {})
  await mutation.mutate({ fail: 'network' }).catch(() => {})
  assert.equal(keys[0], keys[1], '网络失败重试复用同一 key（后端幂等兜底）')
  await mutation.mutate({ fail: 'business' }).catch(() => {})
  await mutation.mutate({ fail: 'business' }).catch(() => {})
  assert.notEqual(keys[2], keys[3], '业务失败后为新的逻辑操作')
  scope.stop()
})

test('成功后精准失效相关查询', async () => {
  const scope = effectScope()
  const seen = []
  let mutation
  scope.run(() => {
    useQuery({
      key: ['documents', 'list', { page: 1 }],
      fetcher: async () => [],
      staleTime: 60000,
    })
    mutation = useMutation({
      mutationFn: async () => ({ ok: true }),
      invalidate: [(key) => { seen.push(key) }],
    })
  })
  await tick()
  await mutation.mutate({})
  assert.ok(seen.some((key) => key.startsWith('["documents","list"')), 'invalidate 谓词收到匹配 key')
  scope.stop()
})

test('If-Match 版本头透传 + 409 冲突识别（禁止静默覆盖）', async () => {
  const scope = effectScope()
  let sentIfMatch = null
  let capturedError = null
  let mutation
  scope.run(() => {
    mutation = useMutation({
      mutationFn: async (vars, ctx) => {
        sentIfMatch = ctx.ifMatch
        throw { response: { status: 409, data: { error: { code: 'CONCURRENT_UPDATE_CONFLICT', detail: '并发更新冲突' } } } }
      },
    })
  })
  await mutation.mutate({ name: 'x' }, { ifMatch: '"v3"' }).catch((error) => { capturedError = error })
  assert.equal(sentIfMatch, '"v3"')
  assert.equal(capturedError.kind, 'conflict')
  assert.equal(capturedError.code, 'CONCURRENT_UPDATE_CONFLICT')
  assert.equal(capturedError.message.includes('已'), true, '冲突文案面向用户')
  scope.stop()
})
