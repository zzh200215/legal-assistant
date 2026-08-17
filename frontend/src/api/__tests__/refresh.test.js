import test from 'node:test'
import assert from 'node:assert/strict'
import { createRefreshCoordinator } from '../refresh.js'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

test('并发 401 只触发一次刷新，全部恢复', async () => {
  let refreshes = 0
  let expired = 0
  const coordinator = createRefreshCoordinator({
    refreshFn: async () => {
      refreshes += 1
      await sleep(10)
      return 'new-token'
    },
    onExpired: () => { expired += 1 },
  })
  const results = await Promise.all([coordinator.recover(), coordinator.recover(), coordinator.recover()])
  assert.equal(refreshes, 1, '并发 401 共享同一个 refresh')
  assert.deepEqual(results, [true, true, true])
  assert.equal(expired, 0)
})

test('刷新失败：onExpired 只触发一次，全部失败', async () => {
  let refreshes = 0
  let expired = 0
  const coordinator = createRefreshCoordinator({
    refreshFn: async () => {
      refreshes += 1
      throw new Error('refresh token invalid')
    },
    onExpired: () => { expired += 1 },
  })
  const results = await Promise.all([coordinator.recover(), coordinator.recover()])
  assert.equal(refreshes, 1)
  assert.deepEqual(results, [false, false])
  assert.equal(expired, 1, '按现有认证流程只登出一次')
})

test('刷新成功后再次 401 可再次刷新（in-flight 已重置）', async () => {
  let refreshes = 0
  const coordinator = createRefreshCoordinator({
    refreshFn: async () => {
      refreshes += 1
      return 't'
    },
    onExpired: () => {},
  })
  await coordinator.recover()
  await coordinator.recover()
  assert.equal(refreshes, 2)
})
