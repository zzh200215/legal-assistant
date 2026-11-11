// 通知偏好表单模型：API 存 channels_json 字符串，页面用布尔/数组；这里集中做双向转换与校验。
// 站内通知始终创建（后端 _resolve_channels 强制包含 site），页面只让用户控制邮件/扩展渠道。

export const EVENT_TYPES = [
  { value: 'all', label: '默认（全部事件）', hint: '没有单独配置的事件类型回退到这里' },
  { value: 'deadline', label: '关键日期', hint: '任务与关键日期到期提醒' },
  { value: 'approval', label: '审核', hint: '审核分配与处理结果' },
  { value: 'invoice', label: '账单', hint: '账单、回款与催收' },
  { value: 'sign', label: '签署', hint: '签署请求与回调' },
  { value: 'portal', label: '客户门户', hint: '客户门户访问与动作' },
  { value: 'workflow', label: '案件工作流', hint: '案件动作与协作通知' },
]

export const TIMEZONES = [
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Los_Angeles',
  'UTC',
]

export const SUMMARY_FREQUENCIES = [
  { value: 'none', label: '不汇总' },
  { value: 'daily', label: '每日汇总' },
  { value: 'weekly', label: '每周汇总' },
]

const KNOWN_CHANNELS = ['site', 'email']

export function parseChannels(raw) {
  if (raw === null || raw === undefined || raw === '') return null
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    return parsed.filter((item) => typeof item === 'string')
  } catch {
    return null
  }
}

export function toPreferenceForm(pref) {
  const parsed = pref ? parseChannels(pref.channels_json) : null
  const channels = parsed ?? (pref ? [] : ['site', 'email'])
  return {
    email: channels.includes('email'),
    extraChannels: channels.filter((item) => !KNOWN_CHANNELS.includes(item)),
    mute_start: pref?.mute_start || '',
    mute_end: pref?.mute_end || '',
    timezone: pref?.timezone || 'Asia/Shanghai',
    delegate_user_id: pref?.delegate_user_id ?? null,
    delegate_start: pref?.delegate_start || '',
    delegate_end: pref?.delegate_end || '',
    summary_frequency: pref?.summary_frequency || 'none',
  }
}

export function buildPreferencePayload(form) {
  const channels = Array.from(
    new Set(['site', ...(form.email ? ['email'] : []), ...(form.extraChannels || [])]),
  )
  return {
    channels_json: JSON.stringify(channels),
    // 空串转 null：后端用显式 null 表示清除静默时段。
    mute_start: form.mute_start || null,
    mute_end: form.mute_end || null,
    timezone: form.timezone,
    delegate_user_id: form.delegate_user_id ?? null,
    delegate_start: form.delegate_start || null,
    delegate_end: form.delegate_end || null,
    summary_frequency: form.summary_frequency || 'none',
  }
}

export function validatePreferenceForm(form) {
  const start = form.mute_start || ''
  const end = form.mute_end || ''
  if (Boolean(start) !== Boolean(end)) return '请同时填写静默开始和结束时间'
  if (start && start === end) return '静默开始和结束不能相同'
  const delegateStart = form.delegate_start || ''
  const delegateEnd = form.delegate_end || ''
  if (delegateStart && delegateEnd && delegateStart > delegateEnd) {
    return '代理开始日期不能晚于结束日期'
  }
  if (!form.timezone) return '请选择时区'
  return ''
}
