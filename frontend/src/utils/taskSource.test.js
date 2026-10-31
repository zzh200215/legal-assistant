import test from 'node:test'
import assert from 'node:assert/strict'

import { TASK_SOURCE_LABELS, taskSourceLabel } from './taskSource.js'

test('任务来源：已知枚举返回中文标签', () => {
  assert.equal(taskSourceLabel('document'), '文档')
  assert.equal(taskSourceLabel('legal_consultation'), '咨询')
  assert.equal(taskSourceLabel('legal_review'), '合同审查')
  assert.equal(taskSourceLabel('meeting'), '会议')
})

test('任务来源：未知枚举保留原值，不吞信息', () => {
  assert.equal(taskSourceLabel('connector_sync'), 'connector_sync')
})

test('任务来源：空值返回空串，不渲染占位标签', () => {
  assert.equal(taskSourceLabel(null), '')
  assert.equal(taskSourceLabel(''), '')
  assert.equal(taskSourceLabel(undefined), '')
})

test('任务来源：映射表冻结，调用方不能就地改坏', () => {
  assert.equal(Object.isFrozen(TASK_SOURCE_LABELS), true)
})
