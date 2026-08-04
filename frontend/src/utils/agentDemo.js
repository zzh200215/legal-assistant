export const AGENT_DEMO_PRESETS = {
  document_risk: {
    key: 'document_risk',
    title: '标准演示链路：总结文档并提取风险',
    description: '这条链路用于同时展示文档分析能力、Agent 规划能力和工具执行日志，适合面试或项目演示时稳定复用。',
    maxSteps: 5,
    prepChecklist: [
      '先在文档工作台完成上传，确保文档已经建立索引。',
      '优先选择包含付款、交付、责任或期限条款的合同、方案文档。',
      '可直接执行；文档分析会自动继续，只有创建任务等敏感动作才会弹出确认。',
    ],
    expectedFlow: [
      'document_summary_tool：先总结文档背景、目标和关键内容。',
      'document_risk_tool：提取高风险事项、风险说明和建议动作。',
      'finish：汇总摘要和风险结论，形成最终答复。',
    ],
    successCriteria: [
      '计划预览里能看到文档总结 -> 风险提取 -> finish 这条路径。',
      '执行时间线里能看到每一步的工具名、耗时、状态和 observation。',
      '最终答复里能清楚交代摘要结论、风险数量和重点风险。',
    ],
  },
}

export const buildDocumentRiskGoal = (documentId) => `总结文档 ${documentId}，并提取其中的风险点`

export const getAgentDemoPreset = (demoType, context = {}) => {
  const preset = AGENT_DEMO_PRESETS[demoType]
  if (!preset) return null
  const documentId = Number(context.documentId)
  const hasDocumentId = Number.isFinite(documentId) && documentId > 0
  return {
    ...preset,
    documentId: hasDocumentId ? documentId : null,
    documentTitle: String(context.documentTitle || ''),
    goal: hasDocumentId ? buildDocumentRiskGoal(documentId) : '',
  }
}

export const buildAgentDemoRouteQuery = (demoType, context = {}) => {
  const preset = getAgentDemoPreset(demoType, context)
  if (!preset || !preset.documentId) return {}
  return {
    demo: preset.key,
    documentId: String(preset.documentId),
    documentTitle: preset.documentTitle || '',
    retryGoal: preset.goal,
    maxSteps: String(preset.maxSteps),
  }
}
