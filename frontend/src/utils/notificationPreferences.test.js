import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildPreferencePayload,
  parseChannels,
  toPreferenceForm,
  validatePreferenceForm,
} from './notificationPreferences.js'

test('parseChannels：合法数组保留，坏 JSON/对象返回 null', () => {
  assert.deepEqual(parseChannels('["site","email"]'), ['site', 'email'])
  assert.equal(parseChannels('not-json'), null)
  assert.equal(parseChannels('{"site":true}'), null)
  assert.equal(parseChannels(null), null)
})

test('toPreferenceForm：没有偏好行时默认站内 + 邮件', () => {
  const form = toPreferenceForm(null)
  assert.equal(form.email, true)
  assert.deepEqual(form.extraChannels, [])
  assert.equal(form.timezone, 'Asia/Shanghai')
  assert.equal(form.summary_frequency, 'none')
})

test('toPreferenceForm：已有偏好只开站内时 email=false，扩展渠道保留', () => {
  const form = toPreferenceForm({
    channels_json: '["site","wechat"]',
    mute_start: '22:00',
    mute_end: '07:00',
    timezone: 'Asia/Tokyo',
    delegate_user_id: 7,
    delegate_start: '2026-11-10',
    delegate_end: '2026-11-20',
    summary_frequency: 'daily',
  })
  assert.equal(form.email, false)
  assert.deepEqual(form.extraChannels, ['wechat'])
  assert.equal(form.mute_start, '22:00')
  assert.equal(form.timezone, 'Asia/Tokyo')
  assert.equal(form.delegate_user_id, 7)
  assert.equal(form.delegate_start, '2026-11-10')
  assert.equal(form.delegate_end, '2026-11-20')
  assert.equal(form.summary_frequency, 'daily')
})

test('buildPreferencePayload：站内始终保留，空静默转 null', () => {
  const payload = buildPreferencePayload({
    email: false,
    extraChannels: ['feishu'],
    mute_start: '',
    mute_end: '',
    timezone: 'UTC',
    delegate_user_id: null,
    summary_frequency: 'weekly',
  })
  assert.deepEqual(JSON.parse(payload.channels_json), ['site', 'feishu'])
  assert.equal(payload.mute_start, null)
  assert.equal(payload.mute_end, null)
  assert.equal(payload.timezone, 'UTC')
  assert.equal(payload.summary_frequency, 'weekly')
})

test('buildPreferencePayload：邮件开关与扩展渠道去重', () => {
  const payload = buildPreferencePayload({
    email: true,
    extraChannels: ['email', 'wechat'],
    mute_start: '22:00',
    mute_end: '07:00',
    timezone: 'Asia/Shanghai',
    delegate_user_id: 3,
    delegate_start: '2026-11-10',
    delegate_end: '2026-11-20',
    summary_frequency: 'none',
  })
  assert.deepEqual(JSON.parse(payload.channels_json), ['site', 'email', 'wechat'])
  assert.equal(payload.mute_start, '22:00')
  assert.equal(payload.delegate_user_id, 3)
  assert.equal(payload.delegate_start, '2026-11-10')
  assert.equal(payload.delegate_end, '2026-11-20')
})

test('validatePreferenceForm：代理开始日期不能晚于结束日期', () => {
  const base = { mute_start: '', mute_end: '', timezone: 'Asia/Shanghai' }
  assert.equal(validatePreferenceForm({ ...base, delegate_start: '2026-11-10', delegate_end: '2026-11-20' }), '')
  assert.match(
    validatePreferenceForm({ ...base, delegate_start: '2026-11-21', delegate_end: '2026-11-20' }),
    /不能晚于/,
  )
})

test('validatePreferenceForm：静默必须成对、不能相同、时区必填', () => {
  const base = { mute_start: '', mute_end: '', timezone: 'Asia/Shanghai' }
  assert.equal(validatePreferenceForm(base), '')
  assert.match(validatePreferenceForm({ ...base, mute_start: '22:00' }), /同时填写/)
  assert.match(validatePreferenceForm({ ...base, mute_end: '07:00' }), /同时填写/)
  assert.match(validatePreferenceForm({ ...base, mute_start: '22:00', mute_end: '22:00' }), /不能相同/)
  assert.match(validatePreferenceForm({ ...base, timezone: '' }), /时区/)
})
