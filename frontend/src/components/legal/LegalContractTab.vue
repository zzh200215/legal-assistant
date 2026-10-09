<template>
  <div class="contract-workspace">
    <section v-if="!contractResult" class="contract-intake">
      <div class="intake-heading">
        <div><h2>开始合同审查</h2><p>导入合同或粘贴原文，审查结果会归档到当前案件。</p></div>
      </div>
      <label class="field-label" for="contract-title">合同名称</label>
      <el-input id="contract-title" v-model="contractForm.title" placeholder="例如：技术服务合同" />
      <label class="field-label" for="contract-content">合同原文</label>
      <el-input id="contract-content" v-model="contractForm.content" type="textarea" :rows="13" placeholder="粘贴合同全文或主要条款..." maxlength="50000" show-word-limit />
      <div class="intake-actions">
        <el-upload :show-file-list="false" :before-upload="handleContractUpload" accept=".pdf,.docx,.doc,.txt,.md">
          <el-button :loading="uploadLoading">上传合同文件</el-button>
        </el-upload>
        <el-button type="primary" :loading="contractLoading" @click="submitContractReview">开始审查</el-button>
      </div>
    </section>

    <template v-else>
      <header class="document-toolbar">
        <div class="document-heading">
          <button class="back-action" type="button" aria-label="新建一次审查" title="新建一次审查" @click="startNewReview">‹</button>
          <div class="document-title-group">
            <h2>{{ contractResult.title || contractForm.title || '未命名合同' }}</h2>
            <div class="document-meta">
              <span>{{ statusLabel(contractResult.status) }}</span>
              <span>Version {{ contractResult.version || 1 }}</span>
              <span v-if="contractResult.updated_at">{{ formatDate(contractResult.updated_at) }}</span>
            </div>
          </div>
        </div>
        <div class="document-actions">
          <el-button size="small" @click="exportReview">导出审查意见</el-button>
          <el-button size="small" type="primary" :loading="reviewSubmitting" :disabled="!canSubmitForReview" @click="submitForLawyerReview">提交律师审核</el-button>
        </div>
      </header>

      <p v-if="contractResult.summary" class="review-summary">{{ contractResult.summary }}</p>

      <div class="contract-review-split">
        <section class="document-pane">
          <div class="pane-heading"><h3>合同原文</h3><span>{{ documentParagraphCount }} 段</span></div>
          <LegalDocumentViewer :content="documentContent" :highlighted-paragraph="highlightedParagraph" :highlighted-range="selectedRisk?.source_location" @paragraph-click="selectParagraph" />
        </section>

        <aside class="risk-pane">
          <div class="pane-heading risk-pane-heading">
            <h3>风险与问题</h3>
            <span class="risk-counts"><span class="high-count">高 {{ riskCounts.high }}</span><span>中 {{ riskCounts.medium }}</span><span>低 {{ riskCounts.low }}</span></span>
          </div>
          <div class="risk-filters">
            <el-select v-model="riskFilter.level" size="small" clearable placeholder="全部等级">
              <el-option label="高风险" value="high" /><el-option label="中风险" value="medium" /><el-option label="低风险" value="low" />
            </el-select>
            <el-select v-model="riskFilter.clauseType" size="small" clearable placeholder="全部条款">
              <el-option v-for="type in availableClauseTypes" :key="type" :label="clauseLabel(type)" :value="type" />
            </el-select>
          </div>
          <div class="risk-list" aria-label="审查风险列表">
            <RiskIssueItem v-for="issue in filteredRisks" :key="issue.id || `${issue.clause_type}-${issue.source_location?.paragraph}`" :issue="issue" :active="selectedRisk === issue" @select="selectRisk" />
            <div v-if="!filteredRisks.length" class="risk-empty">未发现符合筛选条件的风险项。</div>
          </div>

          <section v-if="selectedRisk" class="risk-detail">
            <p class="detail-kicker">{{ clauseLabel(selectedRisk.clause_type) || selectedRisk.label || '风险说明' }}</p>
            <h4>{{ selectedRisk.description || '需要进一步核对该条款。' }}</h4>
            <div class="detail-section">
              <h5>修改建议</h5>
              <p>{{ selectedRisk.suggestion || '请结合交易背景补充具体修改方案。' }}</p>
              <el-button v-if="selectedRisk.suggestion" size="small" text @click="copySuggestionToRevision">加入修订稿参考</el-button>
            </div>
            <div v-if="selectedStructuredRisk" class="risk-disposition">
              <span>处理状态：{{ riskDispositionLabel(selectedStructuredRisk.status) }}</span>
              <div v-if="canResolveRisks" class="risk-disposition-actions">
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('accept')">接受风险</el-button>
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('mitigate')">已修改缓释</el-button>
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('dismiss')">忽略风险</el-button>
              </div>
              <p v-if="selectedStructuredRisk.resolution_note" class="resolution-note">{{ selectedStructuredRisk.resolution_note }}</p>
            </div>
            <div class="detail-section evidence-section">
              <h5>法律依据</h5>
              <EvidenceReference v-for="reference in selectedReferences" :key="reference.source_id || reference.title" :reference="reference" @open="openSourceDetail" />
              <p v-if="!selectedReferences.length" class="no-evidence">此风险项没有关联的法源引用。</p>
            </div>
          </section>

          <div class="human-decision-note">审查结果用于辅助判断，最终意见由律师确认。</div>
        </aside>
      </div>

      <div class="review-footnote">
        <AiOutputFeedback :target-type="'contract_review'" :target-id="contractResult.id" :value="contractResult.feedback_score" @submit="submitReviewFeedback" />
        <span v-if="quotaHint('review')" class="quota-note">{{ quotaHint('review') }}</span>
      </div>

      <details class="version-details">
        <summary>版本记录 <span>Version {{ contractResult.version || 1 }} · {{ contractVersions.length }} 条历史</span></summary>
        <DocumentVersion :versions="contractVersions" @set-base="setDiffBase" @set-target="setDiffTarget" />
        <div v-if="diffBase || diffTarget" class="contract-compare-summary">
          <span>对比起点：{{ diffBase ? `v${diffBase.version}` : '未选择' }}</span>
          <span>对比终点：{{ diffTarget ? `v${diffTarget.version}` : '当前版本' }}</span>
        </div>
        <DocumentDiff v-if="diffBase || diffTarget" :before="diffBase?.content || ''" :after="diffTarget?.content || contractResult.content || ''" :before-version="diffBase?.version" :after-version="diffTarget?.version" />
        <div v-if="revisionDraft !== null" class="revision-workspace">
          <div class="revision-heading"><strong>修订稿</strong><span>修改将进入下一审查版本，原审查结果和版本记录保留。</span></div>
          <el-input v-model="revisionDraft" type="textarea" :rows="10" maxlength="50000" show-word-limit aria-label="修订合同原文" />
          <el-button v-if="contractResult.status === 'returned_for_facts'" type="primary" size="small" :loading="resubmitLoading[contractResult.id]" @click="submitRevisionDraft">提交修订版本并重新审查</el-button>
          <el-button v-else-if="canSubmitForReview" size="small" @click="returnContractForRevision">提交审核并退回修改</el-button>
        </div>
        <div v-if="contractResult.status === 'returned_for_facts'" class="resubmit-editor">
          <span>修订从上方修订稿提交，系统保留上一版本并重新执行审查。</span>
        </div>
      </details>

      <details class="history-details">
        <summary>历史审查 <span>{{ filteredContractReviews.length }} 条</span></summary>
        <div class="history-filters">
          <el-select v-model="reviewFilter.status" size="small" clearable placeholder="全部状态">
            <el-option label="待审核" value="pending_review" /><el-option label="需律师审查" value="needs_lawyer_review" />
            <el-option label="退回补充" value="returned_for_facts" /><el-option label="律师通过" value="lawyer_approved" />
            <el-option label="转线下" value="offline_handled" /><el-option label="已关闭" value="closed" />
          </el-select>
          <el-select v-model="reviewFilter.risk" size="small" clearable placeholder="全部风险等级">
            <el-option label="高风险" value="high" /><el-option label="中风险" value="medium" /><el-option label="低风险" value="low" />
          </el-select>
        </div>
        <div class="review-history-list">
          <details v-for="item in filteredContractReviews" :key="item.id" class="history-item" @toggle="loadHistoryVersions($event, item)">
            <summary><strong>{{ item.title || `合同审查 #${item.id}` }}</strong><span>v{{ item.version || 1 }}</span><span>{{ statusLabel(item.status) }}</span><time>{{ formatDate(item.created_at) }}</time></summary>
            <div class="history-item-body">
              <div v-if="item.status === 'returned_for_facts'" class="resubmit-editor">
                <label class="field-label" :for="`resubmit-history-${item.id}`">修订合同原文</label>
                <el-input :id="`resubmit-history-${item.id}`" v-model="resubmitDraftForm[item.id]" type="textarea" :rows="5" placeholder="修改合同内容后重新提交。" />
                <el-button size="small" type="primary" :loading="resubmitLoading[item.id]" @click="submitContractResubmit(item)">提交修订版本</el-button>
              </div>
              <DocumentVersion :versions="contractVersionMap[item.id] || []" />
            </div>
          </details>
          <div v-if="!filteredContractReviews.length" class="legal-empty">暂无历史审查记录。</div>
        </div>
      </details>
    </template>

    <details class="comparison-details">
      <summary>合同版本对比</summary>
      <div class="comparison-content">
        <div class="compare-grid">
          <div><label class="field-label" for="contract-a-title">合同 A</label><el-input id="contract-a-title" v-model="compareForm.title_a" placeholder="合同名称" /><el-input v-model="compareForm.content_a" class="compare-input" type="textarea" :rows="6" placeholder="粘贴合同 A 原文" maxlength="50000" /></div>
          <div><label class="field-label" for="contract-b-title">合同 B</label><el-input id="contract-b-title" v-model="compareForm.title_b" placeholder="合同名称" /><el-input v-model="compareForm.content_b" class="compare-input" type="textarea" :rows="6" placeholder="粘贴合同 B 原文" maxlength="50000" /></div>
        </div>
        <div class="compare-actions"><el-button type="primary" :loading="compareLoading" @click="submitCompare">开始对比</el-button><el-button v-if="compareResult" @click="exportCompare">导出对比结果</el-button></div>
        <div v-if="compareResult" class="compare-result"><p>{{ compareResult.summary }}</p><div v-for="field in compareResult.fields || []" :key="field.label" class="compare-result-row"><strong>{{ field.label }}</strong><span>{{ field.value_a || '未提及' }}</span><span>{{ field.value_b || '未提及' }}</span><span>{{ field.note || (field.conflict ? '存在差异' : '一致') }}</span></div></div>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/upload/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/button/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import LegalDocumentViewer from './LegalDocumentViewer.vue'
import RiskIssueItem from './RiskIssueItem.vue'
import EvidenceReference from './EvidenceReference.vue'
import DocumentVersion from './DocumentVersion.vue'
import DocumentDiff from './DocumentDiff.vue'
import { useContractReviews } from '../../composables/useContractReviews'
import { useContractRiskPresentation, clauseLabel, formatDate, riskLabel, statusLabel } from '../../composables/useLegalWorkspacePresentation'
import { useContractComparison } from '../../composables/useContractComparison'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'

const props = defineProps({ caseId: { type: Number, default: null }, downloadText: { type: Function, default: null } })
const { quotaHint, loadQuota } = useQuota()
const { openSourceDetail } = useLegalSourceDetail()
const {
  contractForm, contractLoading, contractResult, contractReviews, uploadLoading, contractVersionMap,
  resubmitDraftForm, resubmitLoading, loadContractReviews, submitContractReview: runContractReview,
  onExpandContractReview, submitContractResubmit, handleContractUpload: uploadContractReview,
} = useContractReviews({ client: legalWorkspace, message: ElMessage, caseId: computed(() => props.caseId) })
const {
  reviewFilter, riskFilter, highlightedParagraph, availableClauseTypes, filteredRisks, filteredContractReviews,
  resetRiskFilter, jumpToRisk,
} = useContractRiskPresentation({ contractForm, contractResult, contractReviews })
const { compareForm, compareLoading, compareResult, submitCompare } = useContractComparison({ client: legalWorkspace, message: ElMessage })
const selectedRisk = ref(null)
const structuredRiskItems = ref([])
const riskActionLoading = ref(false)
const revisionDraft = ref(null)
const diffBase = ref(null)
const diffTarget = ref(null)
const canResolveRisks = computed(() => structuredRiskItems.value.some((item) => item.can_resolve))
const selectedStructuredRisk = computed(() => {
  if (!selectedRisk.value) return null
  return structuredRiskItems.value.find((item) => item.id === selectedRisk.value.id
    || item.original_text_excerpt === selectedRisk.value.source_location?.snippet
    || (item.category === selectedRisk.value.clause_type && item.summary === selectedRisk.value.description)) || null
})
const riskDispositionLabel = (status) => ({ open: '待处理', needs_review: '待审核', accepted: '已接受', mitigated: '已缓释', dismissed: '已忽略' }[status] || status || '待处理')
const isRiskResolved = (item) => ['accepted', 'mitigated', 'dismissed'].includes(item?.status)
const reviewSubmitting = ref(false)
const documentContent = computed(() => contractResult.value?.content || contractForm.value.content || '')
const documentParagraphCount = computed(() => documentContent.value.split('\n').filter((line) => line.trim()).length)
const contractVersions = computed(() => contractVersionMap.value[contractResult.value?.id] || [])
const riskCounts = computed(() => (contractResult.value?.risks || []).reduce((counts, risk) => {
  if (Object.hasOwn(counts, risk.risk_level)) counts[risk.risk_level] += 1
  return counts
}, { high: 0, medium: 0, low: 0 }))
const selectedReferences = computed(() => selectedRisk.value?.references || selectedRisk.value?.legal_basis || contractResult.value?.references || [])
const canSubmitForReview = computed(() => Boolean(contractResult.value?.id) && !['pending_review', 'needs_lawyer_review', 'lawyer_approved'].includes(contractResult.value?.status))

watch(() => contractResult.value?.risks, (risks) => { selectedRisk.value = risks?.[0] || null }, { deep: true })
watch(() => contractResult.value?.id, (id) => { if (id) onExpandContractReview({ id }) })
watch(() => contractResult.value?.id, async (id) => {
  structuredRiskItems.value = []
  if (!id) return
  try {
    const { data } = await legalWorkspace.listContractRiskItems(id)
    structuredRiskItems.value = data || []
  } catch {
    structuredRiskItems.value = []
  }
})

const submitContractReview = async () => {
  await runContractReview(resetRiskFilter)
  await loadQuota()
}
const handleContractUpload = (file) => uploadContractReview(file, resetRiskFilter)
const selectRisk = (risk) => {
  selectedRisk.value = risk
  jumpToRisk(risk)
}
const copySuggestionToRevision = async () => {
  if (!selectedRisk.value?.suggestion) return
  revisionDraft.value = revisionDraft.value === null ? documentContent.value : revisionDraft.value
  const suggestion = `\n\n【修订参考：${selectedRisk.value.label || '风险条款'}】\n${selectedRisk.value.suggestion}`
  if (!revisionDraft.value.includes(suggestion)) revisionDraft.value += suggestion
  ElMessage.success('已加入修订稿参考')
}
const resolveSelectedRisk = async (action) => {
  if (!contractResult.value?.id || !selectedStructuredRisk.value || riskActionLoading.value) return
  riskActionLoading.value = true
  try {
    const { data } = await legalWorkspace.updateContractRiskItem(contractResult.value.id, selectedStructuredRisk.value.id, { action, note: '合同审查工作区处理' })
    structuredRiskItems.value = structuredRiskItems.value.map((item) => item.id === data.id ? data : item)
    ElMessage.success('风险项状态已更新')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || error.response?.data?.detail || '风险项处理失败')
  } finally {
    riskActionLoading.value = false
  }
}
const setDiffBase = (version) => { diffBase.value = version }
const setDiffTarget = (version) => { diffTarget.value = version }
const submitRevisionDraft = async () => {
  if (!contractResult.value || !revisionDraft.value?.trim()) return ElMessage.warning('请输入修订后的合同内容')
  resubmitDraftForm.value = { ...resubmitDraftForm.value, [contractResult.value.id]: revisionDraft.value }
  await submitContractResubmit(contractResult.value)
  revisionDraft.value = null
  await loadContractReviews()
  contractResult.value = contractReviews.value.find((item) => item.id === contractResult.value.id) || contractResult.value
}
const returnContractForRevision = async () => {
  if (!contractResult.value?.id) return
  try {
    await legalWorkspace.submitLegalReviewAction('contract_review', contractResult.value.id, { action: 'return', note: '请在合同工作区补充修订' })
    contractResult.value.status = 'returned_for_facts'
    revisionDraft.value = documentContent.value
    ElMessage.success('已退回修订')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || error.response?.data?.detail || '退回修订失败')
  }
}
const selectParagraph = (paragraph) => {
  const risk = (contractResult.value?.risks || []).find((item) => item.source_location?.paragraph === paragraph)
  if (risk) selectedRisk.value = risk
}
const loadHistoryVersions = (event, item) => {
  if (event.target.open) onExpandContractReview(item)
}
const startNewReview = () => {
  contractResult.value = null
  contractForm.value = { title: '', content: '' }
  resetRiskFilter()
  structuredRiskItems.value = []
  revisionDraft.value = null
  diffBase.value = null
  diffTarget.value = null
}
const submitForLawyerReview = async () => {
  if (!contractResult.value?.id || reviewSubmitting.value) return
  try {
    await ElMessageBox.confirm('提交后将进入律师审核队列，由审核律师复核并作出最终决定。', '提交律师审核', {
      confirmButtonText: '确认提交', cancelButtonText: '取消', type: 'info',
    })
    reviewSubmitting.value = true
    await legalWorkspace.submitLegalReviewAction('contract_review', contractResult.value.id, { action: 'submit_review', note: '用户提交律师审核' })
    contractResult.value.status = 'needs_lawyer_review'
    await loadContractReviews()
    ElMessage.success('已提交律师审核')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.response?.data?.detail || '提交审核失败')
  } finally {
    reviewSubmitting.value = false
  }
}

const exportReview = () => {
  if (!contractResult.value || !props.downloadText) return
  const result = contractResult.value
  const lines = [`# 合同审查意见书`, '', `**合同标题：** ${result.title || contractForm.value.title || '未命名'}`, `**审查状态：** ${statusLabel(result.status)}`, `**版本：** v${result.version || 1}`, '', '## 审查摘要', result.summary || '', '', '## 条款风险明细']
  if (result.risks?.length) result.risks.forEach((risk, index) => {
    lines.push(`### ${index + 1}. ${risk.label || clauseLabel(risk.clause_type)}（${riskLabel(risk.risk_level)}）`)
    lines.push(`- 风险说明：${risk.description || '无'}`, `- 修改建议：${risk.suggestion || '无'}`)
    if (risk.source_location?.snippet) lines.push(`- 原文：${risk.source_location.snippet}`)
    lines.push('')
  })
  else lines.push('未识别到条款风险。')
  lines.push('', '## 法律依据')
  ;(result.references || []).forEach((reference) => lines.push(`- ${reference.title || '法源'} ${reference.citation || ''} ${reference.version ? `（版本 ${reference.version}）` : ''}`))
  lines.push('', '---', '*辅助审查结果，最终意见由律师确认。*')
  props.downloadText(`${result.title || '合同审查意见书'}.md`, lines.join('\n'))
}

const exportCompare = () => {
  if (!compareResult.value || !props.downloadText) return
  const result = compareResult.value
  const lines = ['# 合同对比结果', '', `**合同 A：** ${compareForm.value.title_a || '合同 A'}`, `**合同 B：** ${compareForm.value.title_b || '合同 B'}`, '', result.summary || '', '', '## 差异明细']
  ;(result.fields || []).forEach((field) => lines.push(`- ${field.label}：A ${field.value_a || '未提及'}；B ${field.value_b || '未提及'}；${field.note || (field.conflict ? '存在差异' : '一致')}`))
  props.downloadText('合同对比结果.md', lines.join('\n'))
}

const submitReviewFeedback = async (score, note) => {
  if (!contractResult.value?.id) return
  try {
    await legalWorkspace.submitReviewFeedback(contractResult.value.id, { score, note })
    contractResult.value.feedback_score = score
    ElMessage.success('反馈已提交')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
  }
}

const openLatestContractReview = async () => {
  await loadContractReviews()
  if (contractResult.value || !contractReviews.value.length) return
  const latest = [...contractReviews.value].sort((a, b) => {
    const left = new Date(a.updated_at || a.created_at || 0).getTime() || Number(a.id) || 0
    const right = new Date(b.updated_at || b.created_at || 0).getTime() || Number(b.id) || 0
    return right - left
  })[0]
  contractResult.value = latest
  contractForm.value = { title: latest.title || '', content: latest.content || '' }
  revisionDraft.value = latest.status === 'returned_for_facts' ? latest.content || '' : null
  await onExpandContractReview(latest)
}

onMounted(openLatestContractReview)
</script>

<style scoped>
.contract-workspace { display: grid; gap: 18px; min-width: 0; }
.contract-intake { display: grid; gap: 10px; max-width: 950px; }
.intake-heading { margin-bottom: 6px; }
.intake-heading h2 { margin: 0; font-size: 17px; font-weight: 600; }
.intake-heading p { margin: 5px 0 0; color: var(--color-text-muted); font-size: 13px; }
.field-label { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 12px; font-weight: 550; }
.intake-actions, .document-actions, .compare-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; }
.intake-actions { justify-content: flex-end; padding-top: 6px; }
.document-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding-bottom: 14px; border-bottom: 1px solid var(--color-border); }
.document-heading { display: flex; align-items: center; gap: 12px; min-width: 0; }
.back-action { width: 30px; height: 30px; border: 1px solid var(--color-border); border-radius: 4px; background: white; color: var(--color-text-secondary); font-size: 21px; line-height: 1; cursor: pointer; }
.document-title-group { min-width: 0; }
.document-title-group h2 { overflow: hidden; margin: 0 0 5px; color: var(--color-text); font-size: 16px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.document-meta { display: flex; flex-wrap: wrap; gap: 10px; color: var(--color-text-muted); font-size: 11px; }
.document-meta span + span::before { margin-right: 10px; color: var(--color-border-hover); content: '·'; }
.review-summary { max-width: 1000px; margin: -5px 0 0; color: var(--color-text-secondary); font-size: 13px; line-height: 1.7; }
.contract-review-split { display: grid; grid-template-columns: minmax(0, 1.18fr) minmax(340px, 0.82fr); min-height: 680px; border: 1px solid var(--color-border); background: white; }
.document-pane, .risk-pane { display: grid; min-width: 0; grid-template-rows: auto minmax(0, 1fr); }
.document-pane { border-right: 1px solid var(--color-border); }
.pane-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 44px; padding: 0 14px; border-bottom: 1px solid var(--color-border); background: #F8FAFB; }
.pane-heading h3 { margin: 0; color: var(--color-text); font-size: 13px; font-weight: 600; }
.pane-heading > span { color: var(--color-text-muted); font-size: 11px; }
.risk-pane { grid-template-rows: auto auto minmax(180px, 0.8fr) auto minmax(130px, 0.5fr) auto; max-height: 850px; }
.risk-pane-heading { background: #fff; }
.risk-counts { display: flex; gap: 10px; white-space: nowrap; }
.risk-counts span { color: var(--color-text-secondary); }
.risk-counts .high-count { color: var(--color-danger); }
.risk-filters { display: flex; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--color-border-light); }
.risk-filters .el-select { width: 50%; min-width: 0; }
.risk-list { min-height: 0; overflow-y: auto; }
.risk-empty, .no-evidence { padding: 15px; color: var(--color-text-muted); font-size: 12px; line-height: 1.6; }
.risk-detail { overflow-y: auto; padding: 14px; border-top: 1px solid var(--color-border); }
.detail-kicker { margin: 0 0 6px; color: var(--color-text-muted); font-size: 11px; }
.risk-detail h4 { margin: 0 0 15px; color: var(--color-text); font-size: 13px; line-height: 1.65; font-weight: 600; }
.detail-section + .detail-section { margin-top: 16px; }
.detail-section h5 { margin: 0 0 6px; color: var(--color-text-muted); font-size: 11px; font-weight: 550; }
.detail-section p { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.65; white-space: pre-line; }
.detail-section .el-button { margin-top: 8px; padding-left: 0; }
.evidence-section { padding-top: 13px; border-top: 1px solid var(--color-border-light); }
.evidence-section .no-evidence { padding: 3px 0; }
.human-decision-note { padding: 10px 14px; border-top: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: 11px; line-height: 1.5; }
.risk-disposition { display: grid; gap: 8px; margin-top: 16px; padding-top: 13px; border-top: 1px solid var(--color-border-light); color: var(--color-text-secondary); font-size: 11px; }
.risk-disposition-actions { display: flex; flex-wrap: wrap; gap: 7px; }
.resolution-note { margin: 0; color: var(--color-text-muted); font-size: 11px; }
.review-footnote { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.quota-note { color: var(--color-text-muted); font-size: 11px; }
.version-details, .history-details, .comparison-details { border-top: 1px solid var(--color-border); }
.version-details > summary, .history-details > summary, .comparison-details > summary { padding: 13px 2px; color: var(--color-text); font-size: 13px; font-weight: 550; cursor: pointer; list-style-position: inside; }
.version-details > summary span, .history-details > summary span { margin-left: 8px; color: var(--color-text-muted); font-size: 11px; font-weight: 400; }
.resubmit-editor { display: grid; gap: 9px; margin-top: 16px; padding: 14px 0; border-top: 1px solid var(--color-border-light); }
.contract-compare-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0; color: var(--color-text-muted); font-size: 11px; }
.revision-workspace { display: grid; gap: 9px; margin-top: 16px; padding: 14px 0; border-top: 1px solid var(--color-border-light); }
.revision-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: 9px; }
.revision-heading strong { color: var(--color-text); font-size: 13px; }
.revision-heading span { color: var(--color-text-muted); font-size: 11px; }
.history-filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.history-item { border-bottom: 1px solid var(--color-border-light); }
.history-item > summary { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 11px 4px; color: var(--color-text-secondary); font-size: 12px; cursor: pointer; list-style: none; }
.history-item > summary strong { flex: 1; min-width: 140px; color: var(--color-text); font-weight: 550; }
.history-item > summary time { color: var(--color-text-muted); font-size: 11px; }
.history-item-body { padding: 0 8px 14px; }
.comparison-content { padding: 0 0 16px; }
.compare-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.compare-grid > div { display: grid; gap: 8px; align-content: start; }
.compare-input { margin-top: 3px; }
.compare-actions { margin-top: 12px; }
.compare-result { margin-top: 14px; }
.compare-result p { color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.compare-result-row { display: grid; grid-template-columns: 100px repeat(3, minmax(0,1fr)); gap: 10px; padding: 9px 3px; border-bottom: 1px solid var(--color-border-light); font-size: 11px; }
.compare-result-row strong { color: var(--color-text); }
.compare-result-row span { color: var(--color-text-secondary); overflow-wrap: anywhere; }
@media (max-width: 940px) {
  .contract-review-split { grid-template-columns: minmax(0,1fr); }
  .document-pane { min-height: 480px; border-right: 0; border-bottom: 1px solid var(--color-border); }
  .risk-pane { max-height: none; grid-template-rows: auto auto minmax(200px, 0.5fr) auto minmax(140px, 0.4fr) auto; }
}
@media (max-width: 640px) {
  .document-toolbar { align-items: flex-start; flex-direction: column; }
  .document-actions { width: 100%; }
  .contract-review-split { min-height: 0; }
  .document-pane { min-height: 420px; }
  .risk-filters { align-items: stretch; flex-direction: column; }
  .risk-filters .el-select { width: 100%; }
  .risk-counts { gap: 6px; font-size: 10px; }
  .compare-grid { grid-template-columns: minmax(0,1fr); }
  .compare-result-row { grid-template-columns: minmax(0,1fr) minmax(0,1fr); }
}
</style>
