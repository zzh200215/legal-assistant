// 请假代理表单模型：API 返回代理 + 起止日期 + 备注 + 状态，页面用表单对象；这里集中转换与校验。
// 请假是用户级设置，覆盖全部事件类型，优先级高于按事件类型的代理。

export const LEAVE_STATUS_LABELS = {
  none: '未设置',
  scheduled: '已排期',
  active: '请假中',
  expired: '已过期',
}

export function toLeaveForm(data) {
  return {
    delegate_user_id: data?.delegate_user_id ?? null,
    leave_start: data?.leave_start || '',
    leave_end: data?.leave_end || '',
    note: data?.note || '',
  }
}

export function buildLeavePayload(form) {
  return {
    delegate_user_id: form.delegate_user_id ?? null,
    // 空串转 null：后端用显式 null 表示不限日期/无备注。
    leave_start: form.leave_start || null,
    leave_end: form.leave_end || null,
    note: (form.note || '').trim() || null,
  }
}

export function validateLeaveForm(form) {
  if (!form.delegate_user_id) return '请选择请假代理'
  const start = form.leave_start || ''
  const end = form.leave_end || ''
  if (start && end && start > end) return '请假开始日期不能晚于结束日期'
  if ((form.note || '').trim().length > 200) return '请假备注不能超过 200 字'
  return ''
}

export function leaveStatusLabel(status) {
  return LEAVE_STATUS_LABELS[status] || LEAVE_STATUS_LABELS.none
}

export function leaveSummary(data) {
  if (!data || !data.delegate_user_id || data.status === 'none') return ''
  const name = data.delegate_name || `用户 ${data.delegate_user_id}`
  let range = '长期有效'
  if (data.leave_start && data.leave_end) range = `${data.leave_start} 至 ${data.leave_end}`
  else if (data.leave_start) range = `${data.leave_start} 起`
  else if (data.leave_end) range = `至 ${data.leave_end}`
  if (data.status !== 'active' || typeof data.days_left !== 'number') {
    return `代 ${name} 接收 · ${range}`
  }
  const left = data.days_left === 0 ? '今天结束' : `剩余 ${data.days_left} 天`
  return `代 ${name} 接收 · ${range} · ${left}`
}
