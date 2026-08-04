const label = (mapping, value, fallback = '') => mapping[value] || value || fallback

export const documentConflictCaseText = (status) => label({
  pending_confirmation: '待确认', task_created: '已建任务', in_progress: '处理中', resolved: '已解决', false_positive: '误报',
}, status)
export const documentConflictCaseTag = (status) => ({
  pending_confirmation: 'warning', task_created: 'danger', in_progress: 'primary', resolved: 'success', false_positive: 'info',
}[status] || 'info')
export const documentSeverityText = (value) => label({ high: '高', medium: '中', low: '低' }, value, '未标记')
export const documentSeverityTagType = (value) => ({ high: 'danger', medium: 'warning' }[value] || 'info')
export const documentReferenceTypeText = (value) => label({
  risk: '风险依据', todo: '待办依据', clause: '条款依据', field: '字段依据', chunk: '文档片段',
}, value, '引用')
export const documentReferenceTagType = (value) => ({ risk: 'danger', todo: 'warning', clause: 'success', field: 'primary', chunk: 'info' }[value] || 'info')

const TOOL_LABELS = {
  finish: '完成', retry: '重试', document_search_tool: '文档检索', document_summary_tool: '文档总结',
  document_risk_tool: '文档风险提取', meeting_summary_tool: '会议总结', meeting_query_tool: '会议查询',
  meeting_action_tool: '会议行动项转任务', email_writer_tool: '文书生成', task_create_tool: '任务创建',
  task_query_tool: '任务查询', sql_query_tool: 'SQL 查询',
}
const WORKER_LABELS = {
  knowledge_agent: '知识 Agent', meeting_agent: '会议 Agent', data_agent: '数据 Agent', project_agent: '项目管理专家',
  legal_compliance_agent: '法务合规专家', communication_agent: '沟通写作专家', workflow_agent: '流程执行 Agent',
  supervisor_agent: '法律总管 Agent', policy_guardrail: '策略校验节点', document_agent: '知识 Agent（历史名称）',
  task_agent: '流程 Agent（历史名称）', task_email_agent: '流程 Agent（历史名称）', general_agent: '兼容 Agent',
  evidence_verifier_agent: '策略校验节点（历史名称）',
}
export const toolLabel = (name) => label(TOOL_LABELS, name)
export const workerLabel = (name) => label(WORKER_LABELS, name)
export const executionModeLabel = (mode) => label({
  orchestration_only: '编排', read_only: '只读', controlled_read_only: '受控只读', draft_only: '仅草稿', controlled_side_effect: '审批执行',
}, mode, '受控')
export const actionTypeText = (type) => label({ tool_call: '工具调用', finish: '完成', retry: '重试', handoff: 'Worker 交接', verify: '证据核验' }, type, '未知')
export const formatJson = (value) => {
  if (!value) return ''
  try { return JSON.stringify(typeof value === 'string' ? JSON.parse(value) : value, null, 2) } catch { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
}
