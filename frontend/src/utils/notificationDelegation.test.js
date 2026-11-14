import test from 'node:test'
import assert from 'node:assert/strict'

import {
  DELEGATION_SOURCE_LABELS,
  delegationBadge,
  delegationFilterParams,
  delegationSourceLabel,
} from './notificationDelegation.js'

test('delegationSourceLabel：请假/类型/历史/未知', () => {
  assert.equal(delegationSourceLabel('leave'), '请假代收')
  assert.equal(delegationSourceLabel('event'), '类型代收')
  assert.equal(delegationSourceLabel(null), '代收')
  assert.equal(delegationSourceLabel(undefined), '代收')
  assert.equal(delegationSourceLabel('escalation'), '代收')
  assert.deepEqual(Object.keys(DELEGATION_SOURCE_LABELS), ['leave', 'event', 'unknown'])
})

test('delegationBadge：无代收人不渲染', () => {
  assert.equal(delegationBadge(null), '')
  assert.equal(delegationBadge({ on_behalf_of_user_id: null }), '')
  assert.equal(delegationBadge({ on_behalf_of_user_id: null, on_behalf_of_source: 'leave' }), '')
})

test('delegationBadge：带来源与代收人姓名', () => {
  assert.equal(
    delegationBadge({ on_behalf_of_user_id: 9, on_behalf_of_source: 'leave', on_behalf_of_name: '协办律师' }),
    '请假代收 · 协办律师',
  )
  assert.equal(
    delegationBadge({ on_behalf_of_user_id: 9, on_behalf_of_source: 'event', on_behalf_of_name: '主办律师' }),
    '类型代收 · 主办律师',
  )
  // 历史事件没有来源列
  assert.equal(
    delegationBadge({ on_behalf_of_user_id: 9, on_behalf_of_source: null, on_behalf_of_name: '张三' }),
    '代收 · 张三',
  )
  // 姓名缺失兜底
  assert.equal(
    delegationBadge({ on_behalf_of_user_id: 9, on_behalf_of_source: 'leave', on_behalf_of_name: null }),
    '请假代收 · 同事',
  )
})

test('delegationFilterParams：来源筛选映射到请求参数', () => {
  assert.deepEqual(delegationFilterParams(''), {})
  assert.deepEqual(delegationFilterParams('own'), { delegated: false })
  assert.deepEqual(delegationFilterParams('delegated'), { delegated: true })
  assert.deepEqual(delegationFilterParams('leave'), { delegation_source: 'leave' })
  assert.deepEqual(delegationFilterParams('event'), { delegation_source: 'event' })
  assert.deepEqual(delegationFilterParams('unknown-value'), {})
})
