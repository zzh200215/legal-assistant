import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useQuery } from '../useQuery.js'
import { invalidateQueries } from '../cache.js'

// useQuery：同 key 去重、并发共享、invalidate 重取、幂等读重试、4xx 不重试、卸载取消。

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('同 key 并发实例共享 in-flight 请求（去重）', async () => {
  let calls = 0
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const scopeA = effectScope()
  const scopeB = effectScope()
  let qa
  let qb
  scopeA.run(() => {
    qa = useQuery({
      key: ['t', 'concurrent'],
      fetcher: async () => { calls += 1; await gate; return { n: calls } },
      staleTime: 60000,
    })
  })
  scopeB.run(() => {
    qb = useQuery({
      key: ['t', 'concurrent'],
      fetcher: async () => { calls += 1; await gate; return { n: calls } },
      staleTime: 60000,
    })
  })
  await tick()
  assert.equal(calls, 1, '两个实例只发一次请求')
  release()
  await tick()
  await tick()
  assert.equal(calls, 1)
  assert.equal(qa.data.value.n, 1)
  assert.equal(qb.data.value.n, 1)
  scopeA.stop()
  scopeB.stop()
})

test('新鲜缓存复用：第二个实例不重复请求', async () => {
  let calls = 0
  const scopeA = effectScope()
  const scopeB = effectScope()
  let qb
  scopeA.run(() => {
    useQuery({
      key: ['t', 'cache'],
      fetcher: async () => { calls += 1; return { v: calls } },
      staleTime: 60000,
    })
  })
  await tick()
  assert.equal(calls, 1)
  scopeB.run(() => {
    qb = useQuery({
      key: ['t', 'cache'],
      fetcher: async () => { calls += 1; return { v: calls } },
      staleTime: 60000,
    })
  })
  await tick()
  assert.equal(calls, 1)
  assert.equal(qb.data.value.v, 1)
  scopeA.stop()
  scopeB.stop()
})

test('invalidate 触发活跃实例自动重取', async () => {
  let calls = 0
  const scope = effectScope()
  let q
  scope.run(() => {
    q = useQuery({
      key: ['documents', 'list', { page: 1 }],
      fetcher: async () => { calls += 1; return { v: calls } },
      staleTime: 60000,
    })
  })
  await tick()
  assert.equal(calls, 1)
  invalidateQueries((key) => key.startsWith('["documents","list"'))
  await tick()
  await tick()
  assert.equal(calls, 2, 'invalidate 后自动重取')
  assert.equal(q.data.value.v, 2)
  scope.stop()
})

test('幂等读网络错误自动重试（指数退避）', async () => {
  let calls = 0
  const scope = effectScope()
  let q
  scope.run(() => {
    q = useQuery({
      key: ['t', 'retry'],
      fetcher: async () => {
        calls += 1
        if (calls < 3) throw { response: undefined, code: 'ECONNRESET' }
        return { ok: true }
      },
      retry: 2,
      retryDelay: () => 5,
    })
  })
  await sleep(60)
  assert.equal(calls, 3)
  assert.equal(q.status.value, 'success')
  scope.stop()
})

test('4xx 业务错误不重试', async () => {
  let calls = 0
  const scope = effectScope()
  let q
  scope.run(() => {
    q = useQuery({
      key: ['t', 'no-retry'],
      fetcher: async () => {
        calls += 1
        throw { response: { status: 404, data: { error: { code: 'TASK_NOT_FOUND' } } } }
      },
      retry: 3,
      retryDelay: () => 5,
    })
  })
  await tick()
  await tick()
  assert.equal(calls, 1)
  assert.equal(q.status.value, 'error')
  assert.equal(q.error.value.code, 'TASK_NOT_FOUND')
  scope.stop()
})

test('最后一个订阅者离开取消进行中请求', async () => {
  let aborted = false
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const scope = effectScope()
  scope.run(() => {
    useQuery({
      key: ['t', 'abort'],
      fetcher: async ({ signal }) => {
        signal.addEventListener('abort', () => { aborted = true })
        await gate
        if (signal.aborted) throw { __canceled: true }
        return { ok: true }
      },
    })
  })
  await tick()
  scope.stop()
  await tick()
  assert.equal(aborted, true, '卸载后取消进行中请求')
  release()
})

test('enabled 由 false 变为 true 时开始取数', async () => {
  let calls = 0
  const enabled = ref(false)
  const scope = effectScope()
  let q
  scope.run(() => {
    q = useQuery({
      key: ['t', 'enabled'],
      fetcher: async () => { calls += 1; return { ok: true } },
      enabled,
    })
  })
  await tick()
  assert.equal(calls, 0, 'enabled=false 不发起请求')
  enabled.value = true
  await tick()
  await tick()
  assert.equal(calls, 1, 'enabled 变为 true 后自动取数')
  assert.equal(q.status.value, 'success')
  scope.stop()
})

test('共享 key 的实例未全部卸载时不取消请求', async () => {
  let aborted = false
  let release
  const gate = new Promise((resolve) => { release = resolve })
  const scopeA = effectScope()
  const scopeB = effectScope()
  scopeA.run(() => {
    useQuery({
      key: ['t', 'shared-abort'],
      fetcher: async ({ signal }) => {
        signal.addEventListener('abort', () => { aborted = true })
        await gate
        return { ok: true }
      },
    })
  })
  await tick()
  scopeB.run(() => {
    useQuery({
      key: ['t', 'shared-abort'],
      fetcher: async () => ({ ok: true }),
    })
  })
  scopeA.stop()
  await tick()
  assert.equal(aborted, false, '仍有订阅者时保留请求')
  scopeB.stop()
  await tick()
  assert.equal(aborted, true, '全部卸载后取消请求')
  release()
})
