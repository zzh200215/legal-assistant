// 通知中心的代收来源：全局请假代收与按事件类型代收是两条路径，
// 列表用不同标签展示、筛选按来源细分；上线前创建的代收事件没有来源列，按「代收」展示。
// 纯函数集中放这里，页面只负责渲染与请求。

export const DELEGATION_SOURCE_LABELS = {
  leave: '请假代收',
  event: '类型代收',
  unknown: '代收',
}

export function delegationSourceLabel(source) {
  return DELEGATION_SOURCE_LABELS[source] || DELEGATION_SOURCE_LABELS.unknown
}

// 代收标签：无代收人返回空串（页面据此不渲染标签）；姓名缺失时用「同事」兜底。
export function delegationBadge(item) {
  if (!item || !item.on_behalf_of_user_id) return ''
  const name = item.on_behalf_of_name || '同事'
  return `${delegationSourceLabel(item.on_behalf_of_source)} · ${name}`
}

// 来源筛选 → 请求参数：本人/代收沿用 delegated，请假/类型改用 delegation_source，
// 两者不叠加，避免后端 AND 出空结果。
export function delegationFilterParams(value) {
  if (value === 'own') return { delegated: false }
  if (value === 'delegated') return { delegated: true }
  if (value === 'leave' || value === 'event') return { delegation_source: value }
  return {}
}
