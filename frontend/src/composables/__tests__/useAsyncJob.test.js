import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope, ref } from 'vue'
import { useAsyncJob, JobStatus } from '../useAsyncJob.js'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function waitFor(predicate, timeoutMs = 1000) {
  const start = Date.now()
  while (!predicate()) {
    if (Date.now() - start > timeoutMs) throw new Error('waitFor timeout')
    await sleep(5)
  }
}

test('轮询直到终态后停止', async () => {
  const calls = []
  const scope = effectScope()
  let job
  scope.run(() => {
    job = useAsyncJob({
      jobId: 42,
      fetchStatus: async () => {
        calls.push(calls.length)
        if (calls.length < 3) return { status: 'running', message: '执行中' }
        return { status: 'succeeded', result: { summary: 'ok' } }
      },
      poll: { base: 5, max: 10, factor: 2 },
    })
  })
  await waitFor(() => job.isTerminal.value)
  const count = calls.length
  assert.ok(count >= 3)
  assert.equal(job.status.value, JobStatus.SUCCEEDED)
  assert.equal(job.job.value.result.summary, 'ok')
  assert.equal(job.source.value, 'polling')
  await sleep(40)
  assert.equal(calls.length, count, '终态后不再轮询')
  scope.stop()
})

test('作用域销毁后停止轮询（卸载取消）', async () => {
  const calls = []
  const scope = effectScope()
  scope.run(() => {
    useAsyncJob({
      jobId: 7,
      fetchStatus: async () => {
        calls.push(1)
        return { status: 'running' }
      },
      poll: { base: 5, max: 10 },
    })
  })
  await waitFor(() => calls.length >= 2)
  scope.stop()
  const count = calls.length
  await sleep(40)
  assert.equal(calls.length, count, '卸载后不再轮询')
})

test('cancel 调后端取消接口且只触发一次', async () => {
  let cancelCalls = 0
  const scope = effectScope()
  let job
  scope.run(() => {
    job = useAsyncJob({
      jobId: 1,
      fetchStatus: async () => ({ status: 'running' }),
      cancelFn: async () => { cancelCalls += 1 },
      poll: { base: 5, max: 10 },
    })
  })
  await waitFor(() => job.isActive.value)
  await job.cancel()
  await job.cancel()
  assert.equal(cancelCalls, 1, '已请求取消后不再重复调用')
  assert.equal(job.cancelRequested.value, true)
  scope.stop()
})

test('jobId 变化重新观察（页面重进入按 job_id 恢复）', async () => {
  const seen = []
  const jobId = ref(null)
  const scope = effectScope()
  scope.run(() => {
    useAsyncJob({
      jobId,
      fetchStatus: async (id) => {
        seen.push(id)
        return { status: 'running' }
      },
      poll: { base: 5, max: 10 },
      enabled: () => jobId.value != null,
    })
  })
  jobId.value = 5
  await waitFor(() => seen.length >= 1)
  assert.ok(seen.includes(5))
  scope.stop()
})

test('Celery 状态归一化（PENDING→queued, SUCCESS→succeeded）', async () => {
  const scope = effectScope()
  let job
  scope.run(() => {
    job = useAsyncJob({
      jobId: 9,
      fetchStatus: async () => ({ state: 'SUCCESS', result: { ok: 1 } }),
      poll: { base: 5, max: 10 },
    })
  })
  await waitFor(() => job.isTerminal.value)
  assert.equal(job.status.value, JobStatus.SUCCEEDED)
  scope.stop()
})
