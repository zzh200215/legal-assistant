import { computed, ref } from 'vue'

const label = (values, value) => values[value] || value

export const riskTagType = (level) => ({ high: 'danger', medium: 'warning', low: 'success' }[level] || 'info')
export const riskLabel = (level) => label({ high: '高风险', medium: '中风险', low: '低风险' }, level)
export const statusTagType = (status) => ({
  draft: 'info', pending_review: 'warning', needs_lawyer_review: 'danger', needs_facts: 'warning',
  lawyer_approved: 'success', returned_for_facts: 'warning', offline_consultation: 'info', archived: 'info',
}[status] || 'info')
export const statusLabel = (status) => label({
  draft: '草稿', pending_review: '待审核', needs_lawyer_review: '需律师审核', needs_facts: '待补充事实',
  lawyer_approved: '律师通过', returned_for_facts: '退回补充', offline_consultation: '转线下', archived: '已归档',
}, status)
export const targetLabel = (type) => label({ consultation: '法律咨询', contract_review: '合同审查', draft: '文书草稿' }, type)
export const categoryLabel = (category) => label({
  labor_dispute: '劳动争议', contract_dispute: '合同纠纷', private_lending: '民间借贷',
  consumer_dispute: '消费纠纷', other: '其他',
}, category)
export const clauseLabel = (key) => label({
  payment: '付款条款', delivery: '交付与验收', breach: '违约责任', compensation: '赔偿条款',
  confidentiality: '保密义务', ip: '知识产权', termination: '解除与终止', dispute_resolution: '争议解决',
}, key)
export const sourceStatusType = (status) => ({ active: 'success', inactive: 'info', pending_update: 'warning' }[status] || 'info')
export const sourceStatusLabel = (status) => label({ active: '当前有效', inactive: '已失效', pending_update: '待更新' }, status)
export const sourceTypeLabel = (type) => label({
  statute: '法律法规', judicial_interpretation: '司法解释', case_summary: '案例摘要',
  contract_template: '合同模板', legal_article: '法律文章',
}, type)
export const actionLabel = (action) => label({
  approve: '通过', return: '退回补充', offline: '转线下', close: '关闭', submit_review: '提交审核', comment: '批注',
}, action)
export const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : ''

export function useContractRiskPresentation({ contractForm, contractResult, contractReviews }) {
  const reviewFilter = ref({ status: '', risk: '' })
  const riskFilter = ref({ clauseType: '', level: '', sortBy: '' })
  const highlightedParagraph = ref(null)
  const contractContentRef = ref(null)
  const contractParagraphs = computed(() => (contractForm.value.content || '').split('\n').filter((line) => line.trim()))
  const availableClauseTypes = computed(() => Array.from(new Set((contractResult.value?.risks || []).map((risk) => risk.clause_type).filter(Boolean))))
  const filteredRisks = computed(() => {
    let risks = [...(contractResult.value?.risks || [])]
    const { clauseType, level, sortBy } = riskFilter.value
    if (clauseType) risks = risks.filter((risk) => risk.clause_type === clauseType)
    if (level) risks = risks.filter((risk) => risk.risk_level === level)
    const rank = { high: 0, medium: 1, low: 2 }
    if (sortBy === 'risk_desc') risks.sort((a, b) => (rank[a.risk_level] ?? 3) - (rank[b.risk_level] ?? 3))
    if (sortBy === 'paragraph_asc') risks.sort((a, b) => (a.source_location?.paragraph ?? 999) - (b.source_location?.paragraph ?? 999))
    return risks
  })
  const filteredContractReviews = computed(() => contractReviews.value.filter((review) => {
    if (reviewFilter.value.status && review.status !== reviewFilter.value.status) return false
    return !reviewFilter.value.risk || (review.risks || []).some((risk) => risk.risk_level === reviewFilter.value.risk)
  }))
  const resetRiskFilter = () => { riskFilter.value = { clauseType: '', level: '', sortBy: '' } }
  const jumpToRisk = (risk) => {
    const paragraph = risk.source_location?.paragraph
    if (!paragraph) return
    highlightedParagraph.value = paragraph
    document.getElementById(`para-${paragraph}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => { highlightedParagraph.value = null }, 3000)
  }
  return {
    reviewFilter, riskFilter, highlightedParagraph, contractContentRef, contractParagraphs,
    availableClauseTypes, filteredRisks, filteredContractReviews, resetRiskFilter, jumpToRisk,
  }
}
