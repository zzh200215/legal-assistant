<template>
  <section class="workflow-ops">
    <div class="workflow-ops-heading">
      <div>
        <strong>案件工作流运行</strong>
        <span>查看组织内案件动作的执行、失败和取消状态。</span>
      </div>
      <button type="button" class="workflow-ops-refresh" :disabled="loading" @click="loadRuns">刷新</button>
    </div>

    <div class="workflow-ops-toolbar">
      <div class="workflow-ops-filters" role="group" aria-label="工作流状态筛选">
        <button v-for="filter in filters" :key="filter.value" type="button" :class="{ active: statusFilter === filter.value }" @click="statusFilter = filter.value">
          {{ filter.label }}
        </button>
      </div>
      <span class="workflow-ops-updated">{{ lastUpdated ? `更新于 ${formatDate(lastUpdated)}` : '尚未加载' }}</span>
    </div>

    <div class="workflow-ops-summary">
      <span><strong>{{ activeCount }}</strong> 执行中</span>
      <span><strong>{{ failedCount }}</strong> 失败</span>
      <span><strong>{{ cancellingCount }}</strong> 取消中</span>
      <span><strong>{{ pendingDeliveryCount }}</strong> 待发布事件</span>
    </div>

    <div v-if="error" class="workflow-ops-error">{{ error }}</div>
    <div v-else-if="loading && !runs.length" class="workflow-ops-empty">正在加载工作流…</div>
    <div v-else-if="!filteredRuns.length" class="workflow-ops-empty">当前筛选下暂无工作流运行。</div>
    <div v-else class="workflow-ops-list">
      <article v-for="run in filteredRuns" :key="run.id" class="workflow-ops-row">
        <button class="workflow-ops-main" type="button" @click="selectRun(run)">
          <div class="workflow-ops-title">
            <strong>{{ workflowLabel(run.workflow_type) }}</strong>
            <span v-if="run.business_key">{{ run.business_key }}</span>
          </div>
          <div class="workflow-ops-meta">
            <span class="workflow-ops-status" :class="`status-${run.status}`">{{ statusLabel(run.status) }}</span>
            <span>{{ stepLabel(run.current_step) }}</span>
            <span>案件 {{ run.case_id || '未关联' }}</span>
            <time>{{ formatDate(run.updated_at || run.created_at) }}</time>
          </div>
        </button>
        <div class="workflow-ops-progress"><span class="workflow-ops-track"><i :style="{ width: `${progress(run)}%` }"></i></span><small>{{ progress(run) }}%</small></div>
        <div class="workflow-ops-actions">
          <button v-if="canRetry(run)" class="workflow-ops-retry" type="button" :disabled="retryingId === run.id" @click="retryRun(run)">{{ retryingId === run.id ? '提交中' : '重试' }}</button>
          <button v-if="canCancel(run)" class="workflow-ops-cancel" type="button" :disabled="cancellingId === run.id" @click="cancelRun(run)">{{ cancellingId === run.id ? '提交中' : '取消' }}</button>
        </div>
      </article>
    </div>

    <section v-if="selectedRun" class="workflow-ops-detail">
      <div class="workflow-ops-detail-heading">
        <div>
          <strong>{{ workflowLabel(selectedRun.workflow?.workflow_type || selectedRun.workflow_type) }}</strong>
          <span>#{{ selectedRun.workflow?.id || selectedRun.id }}</span>
        </div>
        <button type="button" class="workflow-ops-close" aria-label="关闭工作流详情" @click="selectedRun = null">关闭</button>
      </div>
      <p v-if="selectedRun.workflow?.error_message" class="workflow-ops-detail-error">{{ selectedRun.workflow.error_message }}</p>
      <ol v-if="selectedRun.events?.length" class="workflow-ops-events">
        <li v-for="event in selectedRun.events" :key="event.id">
          <span class="workflow-ops-event-dot"></span>
          <div>
            <strong>{{ eventLabel(event.event_type) }}</strong>
            <span>{{ stepLabel(event.step) }}<template v-if="event.progress != null"> · {{ event.progress }}%</template></span>
            <time>{{ formatDate(event.created_at) }}</time>
          </div>
        </li>
      </ol>
      <p v-else class="workflow-ops-empty">暂无事件记录。</p>
    </section>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { analytics, legalWorkspace } from '../../api'

const runs = ref([])
const statusFilter = ref('all')
const loading = ref(false)
const error = ref('')
const cancellingId = ref(null)
const retryingId = ref(null)
const lastUpdated = ref(null)
const overview = ref(null)
const selectedRun = ref(null)
let timer = null

const filters = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '进行中' },
  { value: 'failed', label: '失败' },
  { value: 'cancelled', label: '已取消' },
]
const statusLabels = { queued: '排队中', running: '执行中', cancelling: '取消中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }
const workflowLabels = { document_parse: '文档解析', document_ingest: '文档入库', contract_review: '合同审查', legal_consultation: '法律咨询', legal_draft: '文书生成', review: '律师审核' }
const filteredRuns = computed(() => {
  if (statusFilter.value === 'active') return runs.value.filter((run) => ['queued', 'running', 'cancelling'].includes(run.status))
  if (statusFilter.value === 'failed') return runs.value.filter((run) => run.status === 'failed')
  if (statusFilter.value === 'cancelled') return runs.value.filter((run) => run.status === 'cancelled')
  return runs.value
})
const activeCount = computed(() => overview.value?.runs?.active ?? runs.value.filter((run) => ['queued', 'running'].includes(run.status)).length)
const failedCount = computed(() => overview.value?.runs?.failed ?? runs.value.filter((run) => run.status === 'failed').length)
const cancellingCount = computed(() => runs.value.filter((run) => run.status === 'cancelling').length)
const pendingDeliveryCount = computed(() => (overview.value?.outbox?.pending || 0) + (overview.value?.outbox?.sending || 0))
const statusLabel = (value) => statusLabels[value] || value || '处理中'
const workflowLabel = (value) => workflowLabels[value] || String(value || '案件工作流').replaceAll('_', ' ')
const stepLabel = (value) => ({ started: '准备中', parsing: '解析文档', chunking: '整理内容', indexing: '建立索引', cancellation_requested: '等待停止', cancelled: '已取消' }[value] || String(value || '处理中').replaceAll('_', ' '))
const eventLabel = (value) => ({ 'workflow.started': '开始执行', 'workflow.succeeded': '执行完成', 'workflow.failed': '执行失败', 'workflow.cancel_requested': '已请求取消', 'workflow.cancelled': '已取消', 'workflow.retry_requested': '已请求重试' }[value] || String(value || '状态更新').replace('workflow.', ''))
const progress = (run) => Math.max(0, Math.min(100, Number(run.progress) || 0))
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'
const canCancel = (run) => ['queued', 'running'].includes(run.status)
const canRetry = (run) => run.status === 'failed'

const loadRuns = async () => {
  loading.value = true
  error.value = ''
  try {
    const [runResult, overviewResult] = await Promise.allSettled([
      legalWorkspace.listWorkflowRuns({ params: { limit: 100 } }),
      analytics.workflowOverview(7),
    ])
    if (runResult.status === 'rejected') throw runResult.reason
    const { data } = runResult.value
    runs.value = Array.isArray(data) ? data : (data?.items || [])
    if (overviewResult.status === 'fulfilled') overview.value = overviewResult.value.data
    lastUpdated.value = new Date().toISOString()
  } catch (err) {
    error.value = err.response?.data?.detail || '工作流运行暂时无法加载'
  } finally {
    loading.value = false
  }
}

const cancelRun = async (run) => {
  cancellingId.value = run.id
  try {
    const { data } = await legalWorkspace.cancelWorkflowRun(run.id)
    const index = runs.value.findIndex((item) => item.id === run.id)
    if (index >= 0 && data) runs.value[index] = data
  } catch (err) {
    error.value = err.response?.data?.detail || '取消工作流失败'
  } finally {
    cancellingId.value = null
  }
}

const retryRun = async (run) => {
  retryingId.value = run.id
  try {
    await legalWorkspace.retryWorkflowRun(run.id)
    await loadRuns()
  } catch (err) {
    error.value = err.response?.data?.detail || '重试工作流失败'
  } finally {
    retryingId.value = null
  }
}

const selectRun = async (run) => {
  selectedRun.value = { ...run, loading: true }
  try {
    const { data } = await legalWorkspace.getWorkflowRun(run.id)
    selectedRun.value = data || run
  } catch {
    selectedRun.value = { ...run, events: [] }
  }
}

onMounted(async () => {
  await loadRuns()
  timer = window.setInterval(() => {
    if (runs.value.some((run) => ['queued', 'running', 'cancelling'].includes(run.status))) loadRuns()
  }, 10000)
})
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
</script>

<style scoped>
.workflow-ops { display: grid; gap: 12px; margin-top: 18px; padding: 16px; border: 1px solid var(--color-border-light); background: var(--color-surface); }
.workflow-ops-heading, .workflow-ops-toolbar { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }
.workflow-ops-heading { padding-bottom: 12px; border-bottom: 1px solid var(--color-border); }.workflow-ops-heading > div { display: grid; gap: 4px; }.workflow-ops-heading strong { color: var(--color-text); font-size: 14px; }.workflow-ops-heading span, .workflow-ops-updated { color: var(--color-text-muted); font-size: 11px; }
.workflow-ops-refresh, .workflow-ops-cancel, .workflow-ops-retry { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }.workflow-ops-refresh:disabled, .workflow-ops-cancel:disabled, .workflow-ops-retry:disabled { color: var(--color-text-muted); cursor: wait; }
.workflow-ops-actions { display: flex; align-items: center; gap: 8px; }
.workflow-ops-filters { display: flex; gap: 12px; }.workflow-ops-filters button { padding: 0 0 4px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-secondary); font: inherit; font-size: 12px; cursor: pointer; }.workflow-ops-filters button.active { border-color: var(--color-primary); color: var(--color-primary); font-weight: 600; }
.workflow-ops-summary { display: flex; gap: 20px; color: var(--color-text-muted); font-size: 11px; }.workflow-ops-summary strong { margin-right: 3px; color: var(--color-text); font-size: 14px; }.workflow-ops-list { display: grid; border-top: 1px solid var(--color-border); }.workflow-ops-row { display: grid; grid-template-columns: minmax(260px, 1.4fr) minmax(120px, .6fr) auto; align-items: center; gap: 16px; min-height: 56px; border-bottom: 1px solid var(--color-border-light); }.workflow-ops-main { display: grid; gap: 5px; min-width: 0; padding: 8px 0; border: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }.workflow-ops-main:hover { background: #F8FAFB; }.workflow-ops-title { display: flex; gap: 8px; min-width: 0; }.workflow-ops-title strong { overflow: hidden; color: var(--color-text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.workflow-ops-title span { overflow: hidden; color: var(--color-text-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }.workflow-ops-meta { display: flex; flex-wrap: wrap; gap: 8px; color: var(--color-text-muted); font-size: 11px; }.workflow-ops-status { padding-left: 7px; border-left: 2px solid var(--color-primary); color: var(--color-text-secondary); }.status-succeeded { border-color: var(--color-success); }.status-failed { border-color: var(--color-danger); }.status-cancelling, .status-cancelled { border-color: var(--color-warning); }.workflow-ops-progress { display: flex; align-items: center; gap: 7px; }.workflow-ops-track { flex: 1; height: 4px; overflow: hidden; background: var(--color-border-light); }.workflow-ops-track i { display: block; height: 100%; background: var(--color-primary); }.workflow-ops-progress small { min-width: 28px; color: var(--color-text-muted); font-size: 10px; text-align: right; }.workflow-ops-empty, .workflow-ops-error { padding: 10px 0; color: var(--color-text-muted); font-size: 12px; }.workflow-ops-error { color: var(--color-danger); }
.workflow-ops-detail { padding-top: 13px; border-top: 1px solid var(--color-border); }.workflow-ops-detail-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }.workflow-ops-detail-heading > div { display: flex; align-items: baseline; gap: 8px; }.workflow-ops-detail-heading strong { color: var(--color-text); font-size: 13px; }.workflow-ops-detail-heading span { color: var(--color-text-muted); font-size: 11px; }.workflow-ops-close { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }.workflow-ops-detail-error { margin: 9px 0 0; color: var(--color-danger); font-size: 12px; }.workflow-ops-events { display: grid; gap: 0; margin: 12px 0 0; padding: 0; list-style: none; }.workflow-ops-events li { display: grid; grid-template-columns: 14px minmax(0, 1fr); gap: 8px; padding-bottom: 10px; }.workflow-ops-events li > div { display: grid; gap: 2px; }.workflow-ops-events strong { font-size: 12px; font-weight: 550; }.workflow-ops-events span, .workflow-ops-events time { color: var(--color-text-muted); font-size: 11px; }.workflow-ops-event-dot { width: 6px; height: 6px; margin: 4px 0 0 3px; border: 1px solid var(--color-primary); border-radius: 50%; background: #fff; }.workflow-ops-events li:not(:last-child) .workflow-ops-event-dot::after { display: block; width: 1px; height: 20px; margin: 6px 0 0 2px; background: var(--color-border); content: ''; }
@media (max-width: 760px) { .workflow-ops-heading, .workflow-ops-toolbar { align-items: flex-start; flex-direction: column; }.workflow-ops-row { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding: 10px 0; }.workflow-ops-progress { grid-column: 1; width: 140px; }.workflow-ops-actions { grid-column: 2; grid-row: 1 / span 2; flex-direction: column; align-items: flex-end; } }
</style>
