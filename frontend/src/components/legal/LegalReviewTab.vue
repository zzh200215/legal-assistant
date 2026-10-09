<template>
  <div class="review-workspace">
    <header class="review-heading">
      <div><p class="review-kicker">人工审核</p><h2>待处理审核</h2><p>AI 提供分析与依据，审核律师作出最终决定。</p></div>
      <div v-if="canReview && reviewStats" class="review-summary-counts"><span><strong>{{ reviewStats.sla?.active_count ?? reviewQueue.length }}</strong> 待处理</span><span><strong>{{ reviewStats.sla?.overdue_count || 0 }}</strong> 逾期</span><span><strong>{{ reviewStats.sla?.unassigned_count || 0 }}</strong> 未分配</span><span><strong>{{ reviewStats.sla?.average_turnaround_hours ?? '—' }}</strong> 平均小时</span></div>
    </header>

    <div class="review-flow"><span>案件信息</span><i>›</i><span>AI 分析结果</span><i>›</i><span>问题与风险</span><i>›</i><span>证据 / 法源</span><i>›</i><span>审核决定</span></div>

    <div class="review-layout">
      <section class="review-queue-pane">
      <div class="queue-heading"><h3>审核队列</h3><span>{{ reviewQueue.length }} 项</span></div>
        <div class="queue-filters">
          <input v-model="queueFilters.search" type="search" placeholder="搜索案件或标题" aria-label="搜索审核队列" />
          <select v-model="queueFilters.status" aria-label="筛选审核状态"><option value="">进行中</option><option value="pending_review,needs_lawyer_review">待律师处理</option><option value="needs_facts">待补充事实</option><option value="lawyer_approved,offline_consultation,archived">已完成</option></select>
          <select v-model="queueFilters.overdue" aria-label="筛选逾期状态"><option value="">全部时限</option><option :value="true">仅逾期</option><option :value="false">未逾期</option></select>
        </div>
        <div v-if="canReview && selectedKeys.length" class="bulk-toolbar">
          <span>已选 {{ selectedKeys.length }} 项</span>
          <select v-model="bulkReviewerId" aria-label="批量分配审核人"><option :value="null">批量分配审核人</option><option v-for="reviewer in reviewers" :key="reviewer.user_id" :value="reviewer.user_id">{{ reviewer.name }}</option></select>
          <button type="button" :disabled="bulkLoading || bulkReviewerId === null" @click="bulkAssign">分配</button>
          <button type="button" :disabled="bulkLoading" @click="bulkAction('approve')">批量通过</button>
          <button type="button" :disabled="bulkLoading" @click="bulkAction('return')">批量退回</button>
          <button type="button" :disabled="bulkLoading" @click="clearSelection">清除</button>
        </div>
        <button v-for="item in reviewQueue" :key="reviewKey(item)" class="queue-item" :class="{ selected: selectedItem === item }" type="button" @click="selectItem(item)">
          <input v-if="canReview" class="queue-check" type="checkbox" :checked="isSelected(item)" :aria-label="`选择${item.title || item.question || item.id}`" @click.stop="toggleSelection(item)" />
          <span class="queue-type">{{ targetLabel(item.target_type) }}</span>
          <strong>{{ item.question || item.title || item.content?.slice(0, 85) || '待审核记录' }}</strong>
          <span class="queue-meta"><span>{{ statusLabel(item.status) }}<template v-if="item.reviewer_id"> · 已分配</template><template v-if="item.review_overdue"> · 逾期</template></span><time>{{ formatDate(item.created_at) }}</time></span>
        </button>
        <div v-if="!reviewQueue.length" class="review-empty">当前没有待处理审核。</div>
      </section>

      <section v-if="selectedItem" class="review-detail-pane">
        <header class="detail-header"><div><span class="detail-kicker">{{ targetLabel(selectedItem.target_type) }}</span><h3>{{ selectedItem.title || selectedItem.question || '审核对象' }}</h3></div><div class="detail-header-actions"><button v-if="selectedItem.case_id" type="button" class="case-link" @click="openCase(selectedItem.case_id)">回到案件</button><span class="detail-status">{{ statusLabel(selectedItem.status) }}</span></div></header>
        <div class="detail-case-context"><span>所属案件</span><strong>{{ selectedItem.case_title || '未关联案件' }}</strong></div>
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
          <button type="button" class="assignment-save" :disabled="assignmentLoading" @click="saveAssignment">{{ assignmentLoading ? '保存中' : '保存分配' }}</button>
          <span v-if="selectedItem.review_overdue" class="overdue-label">已逾期</span>
        </section>
        <div class="detail-grid">
          <section class="detail-block original-block"><h4>提交内容</h4><pre>{{ selectedItem.question || selectedItem.content || selectedItem.title || '无内容' }}</pre></section>
          <section class="detail-block evidence-block"><h4>问题 / 风险</h4><div v-if="selectedItem.risks?.length" class="risk-summary-list"><div v-for="risk in selectedItem.risks" :key="risk.id || risk.description"><span :class="`risk-level level-${risk.risk_level}`">{{ riskLabel(risk.risk_level) }}</span><strong>{{ risk.label || risk.description }}</strong><p>{{ risk.suggestion || risk.description }}</p></div></div><p v-else class="review-muted">风险明细请结合原文与引用依据核对。</p></section>
        </div>

        <section class="detail-block ai-analysis-block"><div class="ai-analysis-heading"><h4>AI分析摘要</h4><span>仅作审核参考</span></div><p>{{ selectedItem.summary || selectedItem.advice || '当前对象未提供单独的分析摘要，请结合提交内容、风险和法源核对。' }}</p><div v-if="missingItems(selectedItem).length" class="missing-facts"><strong>待补充信息</strong><span v-for="item in missingItems(selectedItem)" :key="item">{{ item }}</span></div></section>

        <section class="detail-block evidence-block"><h4>证据 / 法源</h4><EvidenceReference v-for="reference in selectedItem.references || []" :key="reference.source_id || reference.title" :reference="reference" :clickable="false" /><p v-if="!selectedItem.references?.length" class="review-muted">该审核对象没有附带法源引用。</p></section>
        <section class="detail-block timeline-block"><h4>审核历史与批注</h4><ReviewTimeline :entries="reviewHistoryMap[reviewKey(selectedItem)] || []" /><div class="comment-box"><el-input v-model="commentDraft[reviewKey(selectedItem)]" type="textarea" :rows="3" placeholder="记录审核批注，不改变审核状态。" maxlength="2000" /><el-button size="small" :loading="commentLoading[reviewKey(selectedItem)]" @click="submitComment(selectedItem)">添加批注</el-button></div></section>

        <footer v-if="canReview" class="review-actions"><div><strong>审核决定</strong><span>AI 只提供建议，最终结论由审核律师确认。</span></div><div><el-button type="success" @click="reviewAction(selectedItem, 'approve')">通过</el-button><el-button type="warning" @click="reviewAction(selectedItem, 'return')">退回补充</el-button><el-button type="info" @click="reviewAction(selectedItem, 'offline')">转线下</el-button></div></footer>
        <p v-else class="review-readonly-note">当前账号可以查看本人提交的审核记录和批注，最终决定由审核律师完成。</p>
      </section>
      <section v-else class="review-detail-pane review-detail-empty"><span>从左侧选择一项审核对象。</span></section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElInput } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import { ElMessage, ElMessageBox } from 'element-plus'
import { legalWorkspace } from '../../api'
import { useLegalReviewQueue } from '../../composables/useLegalReviewQueue'
import { useCapabilities } from '../../composables/useCapabilities'
import { CAPABILITY } from '../../auth/capabilities'
import { formatDate, riskLabel, statusLabel, targetLabel } from '../../composables/useLegalWorkspacePresentation'
import EvidenceReference from './EvidenceReference.vue'
import ReviewTimeline from './ReviewTimeline.vue'
import { useAuthStore } from '../../stores/auth'

const props = defineProps({ caseId: { type: Number, default: null } })
const router = useRouter()
const { can } = useCapabilities()
const auth = useAuthStore()
const canReview = computed(() => can(CAPABILITY.WORKSPACE_REVIEW) || ['admin', 'reviewer'].includes(auth.currentUser?.legal_role))
const queueFilters = ref({ search: '', status: '', overdue: '' })
const selectedKeys = ref([])
const bulkReviewerId = ref(null)
const bulkLoading = ref(false)
const {
  reviewStats, reviewQueue, reviewHistoryMap, reviewKey, onExpandReview, submitComment, reviewAction,
  commentDraft, commentLoading, loadReviewQueue, loadReviewStats, reviewers, loadReviewers, assignReview,
  bulkAssignReview, bulkReviewAction,
} = useLegalReviewQueue({ client: legalWorkspace, message: ElMessage, prompt: ElMessageBox.prompt, targetLabel, caseId: computed(() => props.caseId), canReview, filters: computed(() => queueFilters.value) })
const selectedItem = ref(null)
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
onMounted(async () => { await loadReviewQueue(); if (canReview.value) { await Promise.all([loadReviewStats(), loadReviewers()]) } })
defineExpose({ refresh: () => Promise.all([loadReviewQueue(), loadReviewStats()]) })
</script>

<style scoped>
.review-workspace { display: grid; gap: 18px; }
.review-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; padding-bottom: 14px; border-bottom: 1px solid var(--color-border); }
.review-kicker, .detail-kicker { margin: 0 0 5px; color: var(--color-text-muted); font-size: 11px; }
.review-heading h2 { margin: 0; font-size: 20px; font-weight: 620; }
.review-heading p:last-child { margin: 5px 0 0; color: var(--color-text-secondary); font-size: 12px; }
.review-summary-counts { display: flex; flex-wrap: wrap; gap: 18px; color: var(--color-text-muted); font-size: 11px; }
.review-summary-counts strong { display: block; margin-bottom: 2px; color: var(--color-text); font-size: 18px; font-weight: 600; }
.review-flow { display: flex; align-items: center; flex-wrap: wrap; gap: 9px; color: var(--color-text-muted); font-size: 11px; }
.review-flow i { color: var(--color-border-hover); font-size: 18px; font-style: normal; }
.review-layout { display: grid; grid-template-columns: minmax(240px, .38fr) minmax(0, 1fr); min-height: 600px; border: 1px solid var(--color-border); background: #fff; }
.review-queue-pane { border-right: 1px solid var(--color-border); }
.queue-heading { display: flex; align-items: baseline; justify-content: space-between; min-height: 45px; padding: 0 14px; border-bottom: 1px solid var(--color-border); background: #F8FAFB; }
.queue-heading h3, .detail-block h4 { margin: 0; font-size: 13px; font-weight: 600; }
.queue-heading span { color: var(--color-text-muted); font-size: 11px; }
.queue-filters { display: grid; gap: 7px; padding: 10px 12px; border-bottom: 1px solid var(--color-border-light); }
.queue-filters input, .queue-filters select, .bulk-toolbar select { width: 100%; height: 30px; padding: 0 7px; border: 1px solid var(--color-border); border-radius: 3px; background: #fff; color: var(--color-text); font: inherit; font-size: 11px; }
.bulk-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; padding: 9px 12px; border-bottom: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: 11px; }
.bulk-toolbar select { width: 145px; }
.bulk-toolbar button { height: 28px; padding: 0 8px; border: 1px solid var(--color-primary); border-radius: 3px; background: #fff; color: var(--color-primary); font: inherit; font-size: 11px; cursor: pointer; }
.bulk-toolbar button:disabled { border-color: var(--color-border); color: var(--color-text-muted); cursor: wait; }
.queue-item { display: grid; width: 100%; gap: 6px; padding: 13px 14px; border: 0; border-bottom: 1px solid var(--color-border-light); background: #fff; color: var(--color-text); text-align: left; cursor: pointer; }
.queue-item:hover { background: #F8FAFB; }
.queue-item.selected { background: #EEF4F8; box-shadow: inset 3px 0 var(--color-primary); }
.queue-check { grid-row: 1 / span 3; align-self: center; accent-color: var(--color-primary); }
.queue-type, .queue-meta, .queue-meta time { color: var(--color-text-muted); font-size: 11px; }
.queue-item strong { overflow: hidden; font-size: 12px; font-weight: 550; line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }
.queue-meta { display: flex; justify-content: space-between; gap: 10px; }
.review-detail-pane { min-width: 0; padding: 18px 22px; }
.detail-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 15px; border-bottom: 1px solid var(--color-border); }
.detail-header h3 { overflow: hidden; margin: 0; font-size: 16px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.detail-header-actions { display: flex; align-items: center; gap: 12px; }
.case-link { padding: 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 11px; cursor: pointer; }
.detail-status { padding-left: 9px; border-left: 2px solid var(--color-warning); color: var(--color-text-secondary); font-size: 11px; white-space: nowrap; }
.detail-case-context { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px 12px; padding: 10px 0; border-bottom: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: 11px; }
.detail-case-context strong { color: var(--color-text); font-size: 12px; font-weight: 600; }
.assignment-bar { display: flex; align-items: end; flex-wrap: wrap; gap: 10px 14px; padding: 11px 0; border-bottom: 1px solid var(--color-border-light); }
.assignment-bar label { display: grid; gap: 4px; color: var(--color-text-muted); font-size: 11px; }
.assignment-bar select, .assignment-bar input { min-width: 150px; height: 30px; padding: 0 7px; border: 1px solid var(--color-border); border-radius: 3px; background: #fff; color: var(--color-text); font: inherit; font-size: 12px; }
.assignment-save { height: 30px; padding: 0 10px; border: 1px solid var(--color-primary); border-radius: 3px; background: #fff; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }.assignment-save:disabled { color: var(--color-text-muted); border-color: var(--color-border); cursor: wait; }.overdue-label { padding-left: 7px; border-left: 2px solid var(--color-danger); color: var(--color-danger); font-size: 11px; }
.detail-grid { display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(240px, .95fr); gap: 22px; padding: 18px 0; }
.detail-block + .detail-block { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--color-border-light); }
.detail-grid .detail-block + .detail-block { margin-top: 0; padding-top: 0; border-top: 0; }
.detail-block h4 { margin-bottom: 10px; }
.ai-analysis-block { padding: 13px 14px; background: #F8FAFB; border-left: 3px solid var(--color-primary); }
.ai-analysis-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.ai-analysis-heading span { color: var(--color-text-muted); font-size: 10px; }
.ai-analysis-block p { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.7; white-space: pre-wrap; }
.missing-facts { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 11px; color: var(--color-text-muted); font-size: 11px; }
.missing-facts strong { color: var(--color-warning); font-weight: 600; }
.missing-facts span { padding: 3px 7px; border: 1px solid #E8D4A7; border-radius: 3px; background: #FFF9EA; color: #8A6B2A; }
.original-block pre { max-height: 260px; margin: 0; padding: 12px; overflow: auto; background: #F8FAFB; color: var(--color-text-secondary); font-family: var(--font-family); font-size: 12px; line-height: 1.75; white-space: pre-wrap; }
.risk-summary-list { display: grid; gap: 10px; }
.risk-summary-list > div { padding: 9px 10px; border-bottom: 1px solid var(--color-border-light); }
.risk-summary-list strong { display: block; margin-top: 5px; font-size: 12px; font-weight: 550; }
.risk-summary-list p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 11px; line-height: 1.55; }
.risk-level { padding-left: 7px; border-left: 2px solid var(--color-info); color: var(--color-text-muted); font-size: 10px; }
.level-high { border-color: var(--color-danger); color: var(--color-danger); }.level-medium { border-color: var(--color-warning); color: var(--color-warning); }.level-low { border-color: var(--color-success); color: var(--color-success); }
.review-muted, .review-empty { color: var(--color-text-muted); font-size: 12px; line-height: 1.6; }
.comment-box { display: grid; gap: 8px; max-width: 560px; margin-top: 12px; }
.review-actions { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 22px; padding-top: 15px; border-top: 1px solid var(--color-border); color: var(--color-text-muted); font-size: 12px; }
.review-actions > div:first-child { display: grid; gap: 4px; }
.review-actions strong { color: var(--color-text); font-weight: 600; }
.review-actions span { font-size: 11px; }
.review-detail-empty { display: grid; place-items: center; color: var(--color-text-muted); font-size: 13px; }
@media (max-width: 820px) { .review-layout { grid-template-columns: minmax(0,1fr); } .review-queue-pane { max-height: 300px; overflow: auto; border-right: 0; border-bottom: 1px solid var(--color-border); } .detail-grid { grid-template-columns: minmax(0,1fr); gap: 0; } .detail-grid .detail-block + .detail-block { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--color-border-light); } }
@media (max-width: 560px) { .review-heading { align-items: flex-start; flex-direction: column; } .review-detail-pane { padding: 15px; } .review-actions { align-items: flex-start; flex-direction: column; } }
</style>
