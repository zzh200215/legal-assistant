<template>
  <div class="review-workspace">
    <!-- 页头：方向 B .app-page-head（标题 + 说明 + 右侧审核指标），替代原「下划线标题 + 计数行」 -->
    <header class="app-page-head">
      <div class="app-page-head-main">
        <p class="review-kicker">人工审核</p>
        <h1 class="app-page-title">待处理审核</h1>
        <p class="app-page-sub">AI 提供分析与依据，审核律师作出最终决定。</p>
      </div>
      <div v-if="canReview && reviewStats" class="app-page-head-actions review-summary-counts"><span><strong>{{ reviewStats.sla?.active_count ?? reviewQueue.length }}</strong> 待处理</span><span><strong>{{ reviewStats.sla?.overdue_count || 0 }}</strong> 逾期</span><span><strong>{{ reviewStats.sla?.due_soon_count || 0 }}</strong> 24h内到期</span><button v-if="noEvidenceConsultations" type="button" class="summary-chip" :class="{ active: queueFilters.evidence === 'none' }" @click="toggleEvidenceFilter"><strong>{{ noEvidenceConsultations }}</strong> 无依据咨询</button><span><strong>{{ reviewStats.sla?.unassigned_count || 0 }}</strong> 未分配</span><span><strong>{{ reviewStats.sla?.average_turnaround_hours ?? '—' }}</strong> 平均小时</span></div>
    </header>

    <div class="review-flow"><span>案件信息</span><i>›</i><span>AI 分析结果</span><i>›</i><span>问题与风险</span><i>›</i><span>证据 / 法源</span><i>›</i><span>审核决定</span></div>

    <div class="review-layout">
      <!-- 左：审核队列（.app-card + 卡片头 / 卡片体） -->
      <section class="app-card review-queue-pane">
        <div class="app-card-head">
          <h2>审核队列</h2>
          <span class="app-card-head-meta">{{ reviewQueue.length }} 项</span>
        </div>
        <div class="app-card-body review-queue-body">
          <div class="queue-filters">
            <input v-model="queueFilters.search" type="search" placeholder="搜索案件或标题" aria-label="搜索审核队列" />
            <select v-model="queueFilters.status" aria-label="筛选审核状态"><option value="">进行中</option><option value="pending_review,needs_lawyer_review">待律师处理</option><option value="needs_facts">待补充事实</option><option value="lawyer_approved,offline_consultation,archived">已完成</option></select>
            <select v-model="queueFilters.overdue" aria-label="筛选逾期状态"><option value="">全部时限</option><option :value="true">仅逾期</option><option value="due_soon">24小时内到期</option><option :value="false">未逾期</option></select>
            <select v-model="queueFilters.evidence" aria-label="筛选依据等级"><option value="">全部依据</option><option value="none">无依据</option><option value="weak">依据弱</option><option value="partial">部分</option><option value="strong">强</option></select>
          </div>
          <div v-if="canReview && selectedKeys.length" class="bulk-toolbar">
            <span class="bulk-count">已选 {{ selectedKeys.length }} 项</span>
            <select v-model="bulkReviewerId" aria-label="批量分配审核人"><option :value="null">批量分配审核人</option><option v-for="reviewer in reviewers" :key="reviewer.user_id" :value="reviewer.user_id">{{ reviewer.name }}</option></select>
            <button type="button" class="workspace-primary is-compact" :disabled="bulkLoading || bulkReviewerId === null" @click="bulkAssign">分配</button>
            <button type="button" class="bulk-action" :disabled="bulkLoading" @click="bulkAction('approve')">批量通过</button>
            <button type="button" class="bulk-action" :disabled="bulkLoading" @click="bulkAction('return')">批量退回</button>
            <button type="button" class="bulk-action" :disabled="bulkLoading" @click="clearSelection">清除</button>
          </div>
          <QueryStateView :status="queueStatus" :error="queueError" :empty-text="queueFilters.evidence ? '没有符合依据筛选的咨询。' : '当前没有待处理审核。'" min-height="120px" @retry="loadReviewQueue">
            <div class="queue-items">
              <button v-for="item in reviewQueue" :key="reviewKey(item)" class="queue-item app-thread" :class="{ 'is-active': selectedItem === item }" type="button" @click="selectItem(item)">
                <span class="queue-item-top">
                  <input v-if="canReview" class="queue-check" type="checkbox" :checked="isSelected(item)" :aria-label="`选择${item.title || item.question || item.id}`" @click.stop="toggleSelection(item)" />
                  <span class="app-thread-title">{{ item.question || item.title || item.content?.slice(0, 85) || '待审核记录' }}</span>
                  <span class="app-tag is-info queue-type">{{ targetLabel(item.target_type) }}</span><span v-if="chainSignal(item)" class="app-tag queue-chain" :class="chainSignal(item).className">{{ chainSignal(item).label }}</span>
                </span>
                <span class="app-thread-meta"><span class="app-tag" :class="statusTagClass(item.status)">{{ statusLabel(item.status) }}</span><span v-if="item.reviewer_id" class="app-tag">已分配</span><span v-if="item.review_overdue" class="app-tag is-danger">逾期</span><span v-else-if="item.sla_state === 'due_soon'" class="app-tag is-warning">24h内到期</span><span v-if="item.review_due_in_hours !== null && item.review_due_in_hours !== undefined" class="queue-due" :class="{ 'is-danger': item.review_overdue }">{{ dueText(item.review_due_in_hours) }}</span><time class="queue-time">{{ formatDate(item.created_at) }}</time></span>
              </button>
            </div>
          </QueryStateView>
        </div>
      </section>

      <!-- 右：审核详情（.app-card + 卡片头 / 卡片体，信息与文案全部保留） -->
      <section v-if="selectedItem" class="app-card review-detail-pane">
        <div class="app-card-head detail-header">
          <h2 class="detail-title">{{ selectedItem.title || selectedItem.question || '审核对象' }}</h2>
          <div class="app-card-head-meta detail-header-actions"><span class="app-tag is-info">{{ targetLabel(selectedItem.target_type) }}</span><span v-if="chainSignal(selectedItem)" class="app-tag queue-chain" :class="chainSignal(selectedItem).className">{{ chainSignal(selectedItem).label }}</span><button v-if="selectedItem.case_id" type="button" class="case-link" @click="openCase(selectedItem.case_id)">回到案件</button><span class="app-tag detail-status" :class="statusTagClass(selectedItem.status)">{{ statusLabel(selectedItem.status) }}</span></div>
        </div>
        <div class="app-card-body detail-body">
          <div class="detail-case-context"><span>所属案件</span><strong>{{ selectedItem.case_title || '未关联案件' }}</strong>
            <label v-if="!selectedItem.case_id" class="associate-case">归入案件
              <select :value="''" :disabled="associatingCase" aria-label="将记录归入案件" @change="associateCase(selectedItem, $event.target.value)">
                <option value="" disabled>选择案件…</option>
                <option v-for="c in cases" :key="c.id" :value="c.id">{{ c.title || `案件 #${c.id}` }}</option>
              </select>
            </label>
          </div>
          <section v-if="canReview" class="assignment-bar">
            <label>审核人
              <select v-model="assignment.reviewer_id">
                <option :value="null">未分配</option>
                <option v-for="reviewer in reviewers" :key="reviewer.user_id" :value="reviewer.user_id">{{ reviewer.name }} · {{ reviewer.role }}</option>
              </select>
            </label>
            <label>截止时间
              <input v-model="assignment.due_at" type="datetime-local">
            </label>
            <button type="button" class="workspace-primary is-compact assignment-save" :disabled="assignmentLoading" @click="saveAssignment">{{ assignmentLoading ? '保存中' : '保存分配' }}</button>
            <span v-if="selectedItem.review_overdue" class="app-tag is-danger overdue-label">已逾期</span><span v-else-if="selectedItem.sla_state === 'due_soon'" class="app-tag is-warning overdue-label">24h内到期</span><span v-if="selectedItem.review_due_in_hours !== null && selectedItem.review_due_in_hours !== undefined" class="queue-due detail-due" :class="{ 'is-danger': selectedItem.review_overdue }">{{ dueText(selectedItem.review_due_in_hours) }}</span>
          </section>
          <div class="detail-grid">
            <section class="app-card-section detail-block original-block"><h3 class="app-card-section-title">提交内容</h3><pre>{{ selectedItem.question || selectedItem.content || selectedItem.title || '无内容' }}</pre></section>
            <section class="app-card-section detail-block evidence-block"><h3 class="app-card-section-title">问题 / 风险</h3><div v-if="selectedItem.risks?.length" class="risk-summary-list"><div v-for="risk in selectedItem.risks" :key="risk.id || risk.description"><span class="app-tag" :class="riskTagClass(risk.risk_level)">{{ riskLabel(risk.risk_level) }}</span><strong>{{ risk.label || risk.description }}</strong><p>{{ risk.suggestion || risk.description }}</p></div></div><p v-else class="review-muted">风险明细请结合原文与引用依据核对。</p></section>
          </div>

          <section class="app-card-section detail-block ai-analysis-block"><div class="ai-analysis-heading"><h3 class="app-card-section-title">AI分析摘要</h3><span class="app-tag is-info">仅作审核参考</span></div><p>{{ selectedItem.summary || selectedItem.advice || '当前对象未提供单独的分析摘要，请结合提交内容、风险和法源核对。' }}</p><div v-if="missingItems(selectedItem).length" class="missing-facts"><strong>待补充信息</strong><span v-for="item in missingItems(selectedItem)" :key="item" class="app-tag is-pending">{{ item }}</span></div></section>

          <section class="app-card-section detail-block evidence-block"><h3 class="app-card-section-title">证据 / 法源</h3><p v-if="selectedItem.evidence_summary" class="chain-detail">{{ chainDetail(selectedItem.evidence_summary) }}</p><EvidenceReference v-for="reference in selectedReferences" :key="reference.source_id || reference.title" :reference="reference" :clickable="false" /><p v-if="!selectedReferences.length" class="review-muted">该审核对象没有附带法源引用。</p></section>
          <section class="app-card-section detail-block timeline-block"><h3 class="app-card-section-title">审核历史与批注</h3><ReviewTimeline :entries="reviewHistoryMap[reviewKey(selectedItem)] || []" /><div class="comment-box"><el-input v-model="commentDraft[reviewKey(selectedItem)]" type="textarea" :rows="3" placeholder="记录审核批注，不改变审核状态。" maxlength="2000" /><el-button size="small" :loading="commentLoading[reviewKey(selectedItem)]" @click="submitComment(selectedItem)">添加批注</el-button></div></section>

          <section
            v-if="canReview && evidenceGaps(selectedItem)"
            class="app-card-section detail-block evidence-gap-block"
            :class="{ 'is-blocking': highRiskGaps(selectedItem) }"
          >
            <h3 class="app-card-section-title">证据链缺口</h3>
            <p v-if="highRiskGaps(selectedItem)" class="evidence-gap-text">
              还有 <strong>{{ highRiskGaps(selectedItem) }}</strong> 条<strong>高风险</strong>没有条款定位——必须回到合同页补齐定位后才能通过，人工勾选不能替代。
              <template v-if="evidenceGaps(selectedItem) > highRiskGaps(selectedItem)">另有 {{ evidenceGaps(selectedItem) - highRiskGaps(selectedItem) }} 条中低风险缺口，可人工核对确认。</template>
            </p>
            <p v-else class="evidence-gap-text">
              这条审查还有 <strong>{{ evidenceGaps(selectedItem) }}</strong> 条中低风险没有条款定位。通过前需人工核对并勾选确认；也可以先退回补充，让提交人补齐定位。
            </p>
            <label v-if="!highRiskGaps(selectedItem)" class="evidence-gap-ack">
              <input v-model="gapAcknowledged[reviewKey(selectedItem)]" type="checkbox" />
              已人工核对无定位风险（{{ evidenceGaps(selectedItem) }} 条）
            </label>
          </section>

          <footer v-if="canReview" class="review-actions"><div><strong>审核决定</strong><span>AI 只提供建议，最终结论由审核律师确认。</span></div><div><el-button type="success" :disabled="!canApprove(selectedItem)" @click="approveReview(selectedItem)">通过</el-button><el-button type="warning" @click="reviewAction(selectedItem, 'return')">退回补充</el-button><el-button type="info" @click="reviewAction(selectedItem, 'offline')">转线下</el-button></div></footer>
          <p v-else class="review-readonly-note">当前账号可以查看本人提交的审核记录和批注，最终决定由审核律师完成。</p>
        </div>
      </section>
      <section v-else class="app-card review-detail-pane review-detail-empty">
        <div class="app-card-body"><span>从左侧选择一项审核对象。</span></div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import 'element-plus/es/components/message-box/style/css'
import { legalWorkspace } from '../../api'
import { useLegalReviewQueue } from '../../composables/useLegalReviewQueue'
import { useCapabilities } from '../../composables/useCapabilities'
import { CAPABILITY } from '../../auth/capabilities'
import { formatDate, riskLabel, statusLabel, targetLabel } from '../../composables/useLegalWorkspacePresentation'
import EvidenceReference from './EvidenceReference.vue'
import ReviewTimeline from './ReviewTimeline.vue'
import QueryStateView from '../QueryStateView.vue'
import { useAuthStore } from '../../stores/auth'
import { asReferenceList } from '../../utils/references'

const props = defineProps({ caseId: { type: Number, default: null } })
const router = useRouter()
const { can } = useCapabilities()
const auth = useAuthStore()
const canReview = computed(() => can(CAPABILITY.WORKSPACE_REVIEW) || ['admin', 'reviewer'].includes(auth.currentUser?.legal_role))
const queueFilters = ref({ search: '', status: '', overdue: '', evidence: '' })
// 咨询证据链信号：none=无依据、weak=依据弱（partial/strong 不打扰队列）
const CHAIN_SIGNALS = {
  none: { label: '无依据', className: 'is-danger' },
  weak: { label: '依据弱', className: 'is-warning' },
}
const chainSignal = (item) => (item?.target_type === 'consultation' ? CHAIN_SIGNALS[item?.evidence_summary?.level] || null : null)
const toggleEvidenceFilter = () => { queueFilters.value.evidence = queueFilters.value.evidence === 'none' ? '' : 'none' }
const displayChainLevel = (summary) => summary?.level || 'none'
const noEvidenceConsultations = computed(() => (reviewQueue.value || []).filter(
  (item) => item.target_type === 'consultation' && displayChainLevel(item.evidence_summary) === 'none',
).length)
const chainDetail = (summary) => {
  const parts = [summary.references ? `引用 ${summary.references} 条` : '无引用']
  if (summary.sentences) parts.push(`句级溯源 ${summary.attributed}/${summary.sentences} 句`)
  else parts.push('未生成句级溯源')
  if (summary.faithfulness === 'pass') parts.push('忠实度通过')
  else if (summary.faithfulness === 'fail') parts.push(`忠实度未通过（${summary.unsupported} 处）`)
  else parts.push('忠实度未校验')
  return parts.join(' · ')
}
// SLA 相对时间：给审核律师「还剩多久 / 超时多久」的直接读数
const dueText = (hours) => {
  if (hours === null || hours === undefined) return ''
  const abs = Math.abs(hours)
  const value = abs < 1 ? `${Math.max(1, Math.round(abs * 60))} 分钟` : abs < 48 ? `${Math.round(abs)} 小时` : `${Math.round(abs / 24)} 天`
  return hours < 0 ? `超时 ${value}` : `剩 ${value}`
}
const selectedKeys = ref([])
const bulkReviewerId = ref(null)
const bulkLoading = ref(false)
// 状态 / 风险 → .app-tag 语义变体（方向 B 的标签只用浅底深字，不用 Element 实心色块）
const statusTagClass = (status) => ({
  lawyer_approved: 'is-ok', offline_consultation: 'is-accent',
  pending_review: 'is-pending', needs_lawyer_review: 'is-pending', needs_facts: 'is-pending', returned_for_facts: 'is-pending',
}[status] || '')
const riskTagClass = (level) => ({ high: 'is-danger', medium: 'is-pending', low: 'is-ok' }[level] || '')
const {
  reviewStats, reviewQueue, queueQuery, reviewHistoryMap, reviewKey, onExpandReview, submitComment, reviewAction,
  commentDraft, commentLoading, loadReviewQueue, loadReviewStats, reviewers, loadReviewers, assignReview,
  bulkAssignReview, bulkReviewAction,
} = useLegalReviewQueue({ client: legalWorkspace, message: ElMessage, prompt: ElMessageBox.prompt, targetLabel, caseId: computed(() => props.caseId), canReview, filters: computed(() => queueFilters.value) })

// 证据链门禁：无条款定位的风险必须人工核对后才能通过（服务端同样校验，前端只负责少走一趟）
const gapAcknowledged = ref({})
const evidenceGaps = (item) => Number(item?.evidence_summary?.unanchored || 0)
const highRiskGaps = (item) => Number(item?.evidence_summary?.unanchored_high || 0)
// 分级门禁：高风险缺口必须补定位（勾选不放行）；中低风险缺口允许人工确认
const canApprove = (item) => highRiskGaps(item) === 0
  && (evidenceGaps(item) === 0 || Boolean(gapAcknowledged.value[reviewKey(item)]))
const approveReview = async (item) => {
  if (highRiskGaps(item)) {
    ElMessage.warning(`还有 ${highRiskGaps(item)} 条高风险没有条款定位，须先补齐定位`)
    return
  }
  if (evidenceGaps(item) && !gapAcknowledged.value[reviewKey(item)]) {
    ElMessage.warning(`还有 ${evidenceGaps(item)} 条中低风险没有条款定位，请先核对并勾选确认`)
    return
  }
  await reviewAction(item, 'approve', evidenceGaps(item) ? { acknowledge_evidence_gaps: true } : {})
}
// 审核队列三态（ux-audit P1-3/M-13）：加载失败与"无待处理"分离，失败可重试
const queueStatus = computed(() => {
  if (queueQuery.isLoading.value) return 'loading'
  if (queueQuery.isError.value) return 'error'
  if (!reviewQueue.value.length) return 'empty'
  return 'success'
})
const queueError = computed(() => (queueQuery.isError.value ? queueQuery.error.value : null))
const selectedItem = ref(null)
// 审核对象携带的 references 可能混入 null/空条目，渲染前统一过滤
const selectedReferences = computed(() => asReferenceList(selectedItem.value?.references))
// 归入案件（ux-audit L-17）：孤立记录（无 case_id）可由提交人或审核律师归档到某个案件
const cases = ref([])
const associatingCase = ref(false)
const loadCases = async () => {
  const orgId = auth.currentUser?.organization_id
  if (!orgId) return
  try {
    const { data } = await legalWorkspace.listCases(orgId)
    cases.value = data || []
  } catch (error) {
    console.error('[review-queue] 案件列表加载失败', error)
  }
}
const associateCase = async (item, caseId) => {
  if (!caseId) return
  associatingCase.value = true
  try {
    await legalWorkspace.associateReviewCase(item.target_type, item.id, Number(caseId))
    ElMessage.success('已归入案件')
    await loadReviewQueue()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.response?.data?.error?.detail || '归入案件失败')
  } finally {
    associatingCase.value = false
  }
}
const missingItems = (item) => [...(item?.missing_facts || []), ...(item?.missing_fields || [])].filter(Boolean)
const openCase = (caseId) => router.push({ path: '/legal-workspace', query: { case_id: String(caseId) } })
const assignment = ref({ reviewer_id: null, due_at: '' })
const assignmentLoading = ref(false)
const isSelected = (item) => selectedKeys.value.includes(reviewKey(item))
const toggleSelection = (item) => { const key = reviewKey(item); selectedKeys.value = isSelected(item) ? selectedKeys.value.filter((value) => value !== key) : [...selectedKeys.value, key] }
const selectedPayload = computed(() => reviewQueue.value.filter((item) => isSelected(item)).map((item) => ({ target_type: item.target_type, target_id: item.id })))
const clearSelection = () => { selectedKeys.value = [] }
const bulkAssign = async () => {
  bulkLoading.value = true
  try { await bulkAssignReview(selectedPayload.value, { reviewer_id: bulkReviewerId.value, due_at: null }); clearSelection() } catch (error) { ElMessage.error(error.response?.data?.error?.detail || '批量分配失败') } finally { bulkLoading.value = false }
}
const bulkAction = async (action) => {
  bulkLoading.value = true
  try { await bulkReviewAction(selectedPayload.value, action, action === 'return' ? '批量退回补充' : '批量审核通过'); clearSelection() } catch (error) { ElMessage.error(error.response?.data?.error?.detail || '批量审核失败') } finally { bulkLoading.value = false }
}
const selectItem = async (item) => { selectedItem.value = item; await onExpandReview(item) }
const toLocalInput = (value) => value ? String(value).slice(0, 16) : ''
const syncAssignment = (item) => { assignment.value = { reviewer_id: item?.reviewer_id || null, due_at: toLocalInput(item?.review_due_at) } }
const saveAssignment = async () => {
  if (!selectedItem.value) return
  assignmentLoading.value = true
  try {
    await assignReview(selectedItem.value, { reviewer_id: assignment.value.reviewer_id || null, due_at: assignment.value.due_at ? new Date(assignment.value.due_at).toISOString() : null })
    selectedItem.value.reviewer_id = assignment.value.reviewer_id || null
    selectedItem.value.review_due_at = assignment.value.due_at ? new Date(assignment.value.due_at).toISOString() : null
  } catch (error) { ElMessage.error(error.response?.data?.detail || error.message || '保存分配失败') } finally { assignmentLoading.value = false }
}
watch(selectedItem, syncAssignment)
watch(reviewQueue, (queue) => {
  if (!queue.length) {
    selectedItem.value = null
    return
  }
  const selectedKey = selectedItem.value ? reviewKey(selectedItem.value) : null
  const next = queue.find((item) => reviewKey(item) === selectedKey) || queue[0]
  if (selectedItem.value !== next) selectItem(next)
}, { immediate: true })
watch(queueFilters, () => { loadReviewQueue() }, { deep: true })
watch(canReview, (allowed, previous) => { if (allowed && !previous) loadReviewStats() })
onMounted(async () => { loadCases(); await loadReviewQueue(); if (canReview.value) { await Promise.all([loadReviewStats(), loadReviewers()]) } })
defineExpose({ refresh: () => Promise.all([loadReviewQueue(), loadReviewStats()]) })
</script>

<style scoped>
/* 版式骨架取自 design-tokens.css 的 .app-* 与 .workspace-primary；这里只补本页排布，
   颜色 / 圆角 / 阴影一律走令牌（预览基准 docs/review/ui-preview-direction-b.html）。 */
.review-workspace { display: grid; gap: var(--space-6); padding-bottom: var(--space-10); }

/* 页头：.app-page-head 形态，左侧标题区、右侧审核指标 */
.review-kicker { margin: 0; color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.04em; }
.review-summary-counts { gap: var(--space-6); margin-left: auto; }
.review-summary-counts > span { display: grid; gap: 2px; color: var(--color-text-muted); font-size: var(--text-xs); }
.review-summary-counts strong { color: var(--color-text); font-size: var(--text-xl); font-weight: 700; line-height: 1.15; }

/* 审核链路：五步文案保持，只做降噪 */
.review-flow { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); }
.review-flow i { color: var(--color-border-hover); font-size: var(--text-lg); font-style: normal; }

/* 左队列 / 右详情：两张 .app-card，靠间距分栏 */
.review-layout { display: grid; grid-template-columns: minmax(260px, .38fr) minmax(0, 1fr); gap: var(--space-6); align-items: start; }
.review-queue-pane, .review-detail-pane { min-width: 0; }

/* 队列卡片体：筛选 + 批量工具条 + 列表，行内留白收紧一点给窄栏更多宽度 */
.review-queue-body { display: grid; gap: var(--space-3); padding: var(--space-4) var(--space-3); }
.queue-filters { display: grid; gap: var(--space-2); }
.bulk-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); color: var(--color-text-muted); font-size: var(--text-xs); }
.bulk-count { font-weight: 600; }
.bulk-toolbar select { flex: 1 1 120px; }

/* 表单控件统一口径：边框 / 圆角 / 底色 / 字号全部走令牌 */
.queue-filters input, .queue-filters select, .bulk-toolbar select, .associate-case select, .assignment-bar select, .assignment-bar input { height: 32px; min-width: 0; padding: 0 var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: var(--text-xs); }
.queue-filters input, .queue-filters select { width: 100%; }
.associate-case select, .assignment-bar select, .assignment-bar input { cursor: pointer; }
.queue-filters input:focus, .queue-filters select:focus, .bulk-toolbar select:focus, .associate-case select:focus, .assignment-bar select:focus, .assignment-bar input:focus { outline: none; border-color: var(--color-primary); box-shadow: 0 0 0 3px var(--color-primary-subtle); }

/* 次级动作（批量通过 / 批量退回 / 清除）：描边按钮，主按钮才用 .workspace-primary */
.bulk-action { min-height: 32px; padding: 0 var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text-secondary); font: inherit; font-size: var(--text-xs); font-weight: 600; cursor: pointer; transition: border-color var(--transition-fast), color var(--transition-fast); }
.bulk-action:hover:not(:disabled) { border-color: var(--color-border-hover); color: var(--color-text); }
.bulk-action:disabled { color: var(--color-text-muted); cursor: wait; }

/* 队列行：.app-thread 皮 + 方向 B 标签（标题单行省略，元信息右对齐日期） */
.queue-items { display: grid; gap: var(--space-2); }
.queue-item-top { display: flex; align-items: center; gap: var(--space-2); min-width: 0; }
.queue-item .app-thread-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.queue-check { flex: 0 0 auto; accent-color: var(--color-primary); }
.queue-type { flex: 0 0 auto; }
.queue-time { flex: 0 0 auto; margin-left: auto; color: var(--color-text-muted); font-size: var(--text-xs); }

/* 详情卡头：标题可省略，右侧标签 / 回到案件 / 状态不换行挤压 */
.detail-header { flex-wrap: wrap; row-gap: var(--space-2); }
.detail-title { min-width: 0; overflow: hidden; font-size: var(--text-lg); text-overflow: ellipsis; white-space: nowrap; }
.detail-header-actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.case-link { padding: 0; border: 0; background: none; color: var(--color-primary); font: inherit; font-size: var(--text-xs); font-weight: 600; cursor: pointer; }
.case-link:hover { color: var(--color-primary-hover); text-decoration: underline; }

/* 详情正文：所属案件 / 分配两条浅底信息条，其余交给 .app-card-section 控制节奏 */
.detail-case-context { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-2) var(--space-3); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); color: var(--color-text-muted); font-size: var(--text-xs); }
.detail-case-context strong { color: var(--color-text); font-size: var(--text-sm); font-weight: 600; }
.associate-case { display: inline-flex; align-items: center; gap: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); }
.assignment-bar { display: flex; align-items: flex-end; flex-wrap: wrap; gap: var(--space-3) var(--space-4); margin-top: var(--space-3); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.assignment-bar label { display: grid; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--text-xs); }
.assignment-bar select, .assignment-bar input { min-width: 170px; }
.overdue-label { flex: 0 0 auto; }

/* 提交内容 / 问题风险：容器不够宽时自动落为单列，不依赖视口断点 */
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: var(--space-5); margin-top: var(--space-5); }
.detail-grid .app-card-section { margin-top: 0; }
.original-block pre { max-height: 260px; margin: 0; padding: var(--space-3) var(--space-4); overflow: auto; border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); color: var(--color-text-secondary); font-family: var(--font-family); font-size: var(--text-sm); line-height: 1.75; white-space: pre-wrap; }
.risk-summary-list { display: grid; gap: var(--space-3); }
.risk-summary-list > div { display: grid; justify-items: start; gap: var(--space-1); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.risk-summary-list strong { color: var(--color-text); font-size: var(--text-sm); font-weight: 600; }
.risk-summary-list p { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); line-height: 1.65; }

/* AI 分析摘要：浅底 + 左侧主色细条，与「仅作参考」的定位一致 */
.ai-analysis-block { padding: var(--space-4) var(--space-5); border: 1px solid var(--color-border-light); border-left: 3px solid var(--color-primary); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.ai-analysis-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin-bottom: var(--space-2); }
.ai-analysis-heading .app-card-section-title { margin: 0; }
.ai-analysis-block p { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.8; white-space: pre-wrap; }
.missing-facts { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-3); color: var(--color-text-muted); font-size: var(--text-xs); }
.missing-facts strong { color: var(--color-warning); font-weight: 600; }

.review-muted { margin: 0; color: var(--color-text-muted); font-size: var(--text-sm); line-height: 1.65; }
.comment-box { display: grid; max-width: 560px; gap: var(--space-2); margin-top: var(--space-4); }
.comment-box :deep(.el-button) { justify-self: start; }

/* 审核决定：详情卡页脚，主 / 次按钮沿用 Element Plus 交互不变 */
.review-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-3); margin-top: var(--space-6); padding-top: var(--space-4); border-top: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: var(--text-sm); }
.review-actions > div:first-child { display: grid; gap: var(--space-1); }
.review-actions strong { color: var(--color-text); font-weight: 600; }
.review-actions span { font-size: var(--text-xs); }
.review-readonly-note { margin: var(--space-5) 0 0; color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.7; }
.review-detail-empty .app-card-body { display: grid; place-items: center; min-height: 240px; color: var(--color-text-muted); font-size: var(--text-sm); }

@media (max-width: 820px) {
  .review-layout { grid-template-columns: minmax(0, 1fr); }
  .review-queue-body { max-height: 340px; overflow: auto; }
  .review-actions { align-items: flex-start; flex-direction: column; }
}
@media (max-width: 640px) {
  .app-page-head-actions { width: 100%; margin-left: 0; }
  .assignment-bar select, .assignment-bar input { min-width: 0; width: 100%; }
}

.evidence-gap-block { border-left: 3px solid var(--color-warning); background: var(--color-warning-light); }
.evidence-gap-block.is-blocking { border-left-color: var(--color-danger); background: var(--color-danger-light); }
.evidence-gap-block.is-blocking .evidence-gap-text strong { color: var(--color-danger); }
.evidence-gap-text { margin: 0 0 var(--space-2); color: var(--color-text-secondary); font-size: var(--text-xs); line-height: 1.7; }
.evidence-gap-text strong { color: var(--color-warning); }
.evidence-gap-ack { display: inline-flex; align-items: center; gap: var(--space-2); color: var(--color-text); font-size: var(--text-xs); cursor: pointer; }

.queue-due { color: var(--color-text-tertiary); font-size: 11px; font-variant-numeric: tabular-nums; }
.queue-due.is-danger { color: var(--color-danger); }
.detail-due { margin-left: var(--space-2); }
.app-tag.is-warning { border-color: var(--color-warning); color: var(--color-warning); }

.queue-chain { flex: 0 0 auto; }
.chain-detail { margin: 0 0 var(--space-2); color: var(--color-text-secondary); font-size: var(--text-xs); }

.summary-chip { display: inline-flex; align-items: baseline; gap: 4px; padding: 2px 8px; border: 1px solid transparent; border-radius: 999px; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.summary-chip:hover { color: var(--color-danger); }
.summary-chip.active { border-color: var(--color-danger); color: var(--color-danger); background: var(--color-danger-light, rgba(214, 69, 69, 0.08)); }
</style>
