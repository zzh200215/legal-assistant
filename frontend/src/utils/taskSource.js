// 任务来源枚举 → 中文标签。任务中心看板/表格与详情面板共用，避免两处映射漂移。
export const TASK_SOURCE_LABELS = Object.freeze({
  document: '文档',
  chat: '聊天',
  meeting: '会议',
  decompose: '拆解',
  legal_consultation: '咨询',
  legal_review: '合同审查',
  contract_review: '合同审查',
})

export function taskSourceLabel(value) {
  if (!value) return ''
  return TASK_SOURCE_LABELS[value] || value
}
