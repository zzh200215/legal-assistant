import test from 'node:test'
import assert from 'node:assert/strict'

import { taskLogActionLabel, taskLogDetail } from './taskLog.js'

test('日志动作：已知动作中文化，未知动作保留原值', () => {
  assert.equal(taskLogActionLabel('created'), '创建任务')
  assert.equal(taskLogActionLabel('assignee_changed'), '负责人变更')
  assert.equal(taskLogActionLabel('priority_changed'), '优先级变更')
  assert.equal(taskLogActionLabel('archived'), '归档任务')
  assert.equal(taskLogActionLabel('unarchived'), '恢复任务')
  assert.equal(taskLogActionLabel('comment_updated'), '编辑评论')
  assert.equal(taskLogActionLabel('comment_deleted'), '删除评论')
  assert.equal(taskLogActionLabel('custom_action'), 'custom_action')
  assert.equal(taskLogActionLabel(''), '')
})

test('日志详情：状态变更翻译状态与拆解标记', () => {
  assert.equal(taskLogDetail('status_changed', 'todo -> in_progress'), '待办 → 进行中')
  assert.equal(
    taskLogDetail('status_changed', 'todo -> in_progress (decomposed)'),
    '待办 → 进行中（拆解触发）'
  )
})

test('日志详情：进度变更加百分比', () => {
  assert.equal(taskLogDetail('progress_changed', '20 -> 50'), '进度 20% → 50%')
  assert.equal(taskLogDetail('progress_changed', '自定义进度'), '自定义进度')
})

test('日志详情：优先级变更加中文标签', () => {
  assert.equal(taskLogDetail('priority_changed', 'high -> low'), '高 → 低')
})

test('日志详情：创建来源复用任务来源标签，空来源归为手动创建', () => {
  assert.equal(taskLogDetail('created', 'Task created (source: document)'), '来源：文档')
  assert.equal(taskLogDetail('created', 'Task created (source: legal_consultation)'), '来源：咨询')
  assert.equal(taskLogDetail('created', 'Task created (source: None)'), '手动创建')
})

test('日志详情：归档/恢复与评论原样回退', () => {
  assert.equal(taskLogDetail('archived', 'Task archived'), '任务已归档')
  assert.equal(taskLogDetail('unarchived', 'Task unarchived'), '任务已恢复')
  assert.equal(taskLogDetail('comment_added', '先核对付款条款'), '先核对付款条款')
  assert.equal(taskLogDetail('created', ''), '')
})
