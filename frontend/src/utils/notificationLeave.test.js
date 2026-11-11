import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildLeavePayload,
  leaveStatusLabel,
  leaveSummary,
  toLeaveForm,
  validateLeaveForm,
} from './notificationLeave.js'

test('toLeaveForm：空数据得到未设置表单', () => {
  assert.deepEqual(toLeaveForm(null), {
    delegate_user_id: null,
    leave_start: '',
    leave_end: '',
    note: '',
  })
})

test('toLeaveForm：回显代理、日期与备注', () => {
  const form = toLeaveForm({
    delegate_user_id: 16,
    delegate_name: '协办律师',
    leave_start: '2026-11-10',
    leave_end: '2026-11-20',
    note: '出差',
    status: 'active',
    days_left: 9,
  })
  assert.equal(form.delegate_user_id, 16)
  assert.equal(form.leave_start, '2026-11-10')
  assert.equal(form.leave_end, '2026-11-20')
  assert.equal(form.note, '出差')
})

test('buildLeavePayload：空串转 null、备注去空格', () => {
  const payload = buildLeavePayload({
    delegate_user_id: 16,
    leave_start: '',
    leave_end: '2026-11-20',
    note: '  出差  ',
  })
  assert.equal(payload.delegate_user_id, 16)
  assert.equal(payload.leave_start, null)
  assert.equal(payload.leave_end, '2026-11-20')
  assert.equal(payload.note, '出差')
})

test('buildLeavePayload：没有代理与备注时下发 null', () => {
  const payload = buildLeavePayload({ delegate_user_id: null, leave_start: '', leave_end: '', note: '   ' })
  assert.equal(payload.delegate_user_id, null)
  assert.equal(payload.note, null)
})

test('validateLeaveForm：必须选代理、日期不倒挂、备注不超长', () => {
  const base = { delegate_user_id: 16, leave_start: '', leave_end: '', note: '' }
  assert.equal(validateLeaveForm(base), '')
  assert.match(validateLeaveForm({ ...base, delegate_user_id: null }), /请选择请假代理/)
  assert.match(
    validateLeaveForm({ ...base, leave_start: '2026-11-21', leave_end: '2026-11-20' }),
    /不能晚于/,
  )
  assert.equal(validateLeaveForm({ ...base, note: '请'.repeat(200) }), '')
  assert.match(validateLeaveForm({ ...base, note: '请'.repeat(201) }), /200/)
})

test('leaveStatusLabel：未知状态回退未设置', () => {
  assert.equal(leaveStatusLabel('active'), '请假中')
  assert.equal(leaveStatusLabel('scheduled'), '已排期')
  assert.equal(leaveStatusLabel('expired'), '已过期')
  assert.equal(leaveStatusLabel('none'), '未设置')
  assert.equal(leaveStatusLabel(undefined), '未设置')
})

test('leaveSummary：有日期/无日期/剩余天数', () => {
  assert.equal(leaveSummary(null), '')
  assert.equal(
    leaveSummary({
      delegate_user_id: 16,
      delegate_name: '协办律师',
      leave_start: '2026-11-10',
      leave_end: '2026-11-20',
      status: 'active',
      days_left: 9,
    }),
    '代 协办律师 接收 · 2026-11-10 至 2026-11-20 · 剩余 9 天',
  )
  assert.equal(
    leaveSummary({ delegate_user_id: 7, leave_start: '2026-11-10', status: 'scheduled' }),
    '代 用户 7 接收 · 2026-11-10 起',
  )
  assert.equal(leaveSummary({ delegate_user_id: 7, status: 'none' }), '')
  assert.equal(
    leaveSummary({
      delegate_user_id: 16,
      delegate_name: '协办律师',
      leave_end: '2026-11-11',
      status: 'active',
      days_left: 0,
    }),
    '代 协办律师 接收 · 至 2026-11-11 · 今天结束',
  )
})
