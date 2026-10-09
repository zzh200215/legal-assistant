<template>
  <section class="workflow-runs-panel" :class="{ compact }">
    <div class="workflow-runs-heading">
      <div>
        <h3>{{ title }}</h3>
        <p v-if="description">{{ description }}</p>
      </div>
      <button class="workflow-refresh" type="button" :disabled="loading" @click="loadRuns">刷新</button>
    </div>

    <div v-if="error" class="workflow-error">{{ error }}</div>
    <div v-else-if="loading && !runs.length" class="workflow-empty">正在加载工作流…</div>
    <div v-else-if="!runs.length" class="workflow-empty">暂无正在处理或最近完成的工作流。</div>
    <div v-else class="workflow-run-list">
      <article v-for="run in runs" :key="run.id" class="workflow-run-row">
        <button class="workflow-run-main" type="button" @click="selectRun(run)">
          <span class="workflow-run-title">
            <strong>{{ workflowLabel(run.workflow_type) }}</strong>
            <span v-if="run.business_key">{{ run.business_key }}</span>
          </span>
          <span class="workflow-run-meta">
            <span class="workflow-status" :class="`status-${run.status}`">{{ statusLabel(run.status) }}</span>
            <span v-if="run.current_step">{{ stepLabel(run.current_step) }}</span>
            <time>{{ formatDate(run.updated_at || run.created_at) }}</time>
          </span>
          <span class="workflow-progress" aria-label="工作流进度">
            <span class="workflow-progress-track"><span :style="{ width: `${progressValue(run)}%` }"></span></span>
            <small>{{ progressValue(run) }}%</small>
          </span>
        </button>
        <div class="workflow-run-actions">
          <button
            v-if="canRetry(run)"
            class="workflow-retry"
            type="button"
            :disabled="retryingId === run.id"
            @click="retryRun(run)"
          >{{ retryingId === run.id ? '提交中' : '重试' }}</button>
          <button
            v-if="canCancel(run)"
            class="workflow-cancel"
            type="button"
            :disabled="cancellingId === run.id"
            @click="cancelRun(run)"
          >{{ cancellingId === run.id ? '提交中' : '取消' }}</button>
        </div>
      </article>
    </div>

    <div v-if="selectedRun" class="workflow-run-detail">
      <div class="workflow-detail-heading">
        <div>
          <strong>{{ workflowLabel(selectedRun.workflow?.workflow_type || selectedRun.workflow_type) }}</strong>
          <span>#{{ selectedRun.workflow?.id || selectedRun.id }}</span>
        </div>
        <button class="workflow-close" type="button" aria-label="关闭详情" @click="selectedRun = null">关闭</button>
      </div>
      <p v-if="selectedRun.workflow?.error_message" class="workflow-detail-error">{{ selectedRun.workflow.error_message }}</p>
      <ol v-if="selectedRun.events?.length" class="workflow-events">
        <li v-for="event in selectedRun.events" :key="event.id">
          <span class="event-dot"></span>
          <div>
            <strong>{{ eventLabel(event.event_type) }}</strong>
            <span>{{ event.step ? stepLabel(event.step) : '' }}<template v-if="event.progress != null"> · {{ event.progress }}%</template></span>
            <time>{{ formatDate(event.created_at) }}</time>
          </div>
        </li>
      </ol>
      <p v-else class="workflow-empty">暂无事件记录。</p>
    </div>
  </section>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { legalWorkspace } from '../../api'

const props = defineProps({
  caseId: { type: [Number, String], default: null },
  workflowId: { type: [Number, String], default: null },
  title: { type: String, default: '工作流进度' },
  description: { type: String, default: '案件动作的执行进度与处理记录。' },
  limit: { type: Number, default: 8 },
  compact: { type: Boolean, default: false },
  poll: { type: Boolean, default: true },
})

const runs = ref([])
const selectedRun = ref(null)
const loading = ref(false)
const error = ref('')
const cancellingId = ref(null)
const retryingId = ref(null)
let timer = null

const statusLabels = {
  queued: '排队中', running: '执行中', cancelling: '取消中',
  succeeded: '已完成', failed: '失败', cancelled: '已取消',
}
const workflowLabels = {
  document_parse: '文档解析', document_ingest: '文档入库', contract_review: '合同审查',
  legal_consultation: '法律咨询', legal_draft: '文书生成', review: '律师审核',
}
const eventLabels = {
  'workflow.started': '开始执行', 'workflow.running': '执行中', 'workflow.succeeded': '执行完成',
  'workflow.failed': '执行失败', 'workflow.cancel_requested': '已请求取消',
  'workflow.cancelled': '已取消', 'workflow.retrying': '准备重试',
  'workflow.retry_requested': '已请求重试',
}

const statusLabel = (value) => statusLabels[value] || value || '处理中'
const workflowLabel = (value) => workflowLabels[value] || String(value || '案件工作流').replaceAll('_', ' ')
const stepLabel = (value) => ({ started: '准备中', parsing: '解析文档', chunking: '整理内容', indexing: '建立资料索引', cancellation_requested: '等待停止', cancelled: '已取消' }[value] || String(value || '').replaceAll('_', ' '))
const eventLabel = (value) => eventLabels[value] || String(value || '状态更新').replace('workflow.', '')
const progressValue = (run) => Math.max(0, Math.min(100, Number(run.progress) || 0))
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'
const canCancel = (run) => ['queued', 'running', 'cancelling'].includes(run.status) && run.status !== 'cancelling'
const canRetry = (run) => run.status === 'failed'

const loadRuns = async () => {
  loading.value = true
  error.value = ''
  try {
    const { data } = await legalWorkspace.listWorkflowRuns({
      params: { case_id: props.caseId || undefined, limit: props.limit },
    })
    runs.value = Array.isArray(data) ? data : (data?.items || [])
    const requestedId = Number(props.workflowId)
    if (requestedId > 0 && (!selectedRun.value || Number(selectedRun.value.workflow?.id || selectedRun.value.id) !== requestedId)) {
      const requested = runs.value.find((item) => Number(item.id) === requestedId)
      if (requested) await selectRun(requested)
      else {
        try {
          const detail = await legalWorkspace.getWorkflowRun(requestedId)
          if (!props.caseId || Number(detail.data?.workflow?.case_id) === Number(props.caseId)) selectedRun.value = detail.data
        } catch {
          // The list is permission-scoped; an unavailable deep link stays closed.
        }
      }
    }
    if (selectedRun.value) {
      const current = runs.value.find((item) => item.id === selectedRun.value.workflow?.id)
      if (current) await selectRun(current, false)
    }
  } catch (err) {
    error.value = err.response?.data?.detail || '工作流进度暂时无法加载'
  } finally {
    loading.value = false
  }
}

const selectRun = async (run, showLoading = true) => {
  if (showLoading) selectedRun.value = { ...run, loading: true }
  try {
    const { data } = await legalWorkspace.getWorkflowRun(run.id)
    selectedRun.value = data || run
  } catch (err) {
    selectedRun.value = { ...run, events: [] }
  }
}

const cancelRun = async (run) => {
  cancellingId.value = run.id
  try {
    const { data } = await legalWorkspace.cancelWorkflowRun(run.id)
    const index = runs.value.findIndex((item) => item.id === run.id)
    if (index >= 0 && data) runs.value[index] = data
    if (selectedRun.value?.workflow?.id === run.id) selectedRun.value.workflow = data
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

const startPolling = () => {
  if (!props.poll) return
  timer = window.setInterval(() => {
    if (runs.value.some((run) => ['queued', 'running', 'cancelling'].includes(run.status))) loadRuns()
  }, 5000)
}

onMounted(async () => { await loadRuns(); startPolling() })
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
watch(() => [props.caseId, props.limit, props.workflowId], loadRuns)
</script>

<style scoped>
.workflow-runs-panel { min-width: 0; padding-top: 18px; border-top: 1px solid var(--color-border); }
.workflow-runs-heading, .workflow-detail-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.workflow-runs-heading h3 { margin: 0; color: var(--color-text); font-size: 15px; font-weight: 600; }
.workflow-runs-heading p { margin: 4px 0 0; color: var(--color-text-muted); font-size: 12px; }
.workflow-refresh, .workflow-close, .workflow-cancel, .workflow-retry { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.workflow-refresh:disabled, .workflow-cancel:disabled { color: var(--color-text-muted); cursor: wait; }
.workflow-retry:disabled { color: var(--color-text-muted); cursor: wait; }
.workflow-run-actions { display: flex; align-items: center; gap: 8px; }
.workflow-run-list { display: grid; margin-top: 10px; }
.workflow-run-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px; min-height: 58px; border-bottom: 1px solid var(--color-border-light); }
.workflow-run-main { display: grid; grid-template-columns: minmax(150px, 1.2fr) minmax(160px, 1fr) minmax(100px, .7fr); align-items: center; gap: 14px; min-width: 0; padding: 8px 0; border: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.workflow-run-main:hover { background: #F8FAFB; }
.workflow-run-title, .workflow-run-meta { display: grid; gap: 3px; min-width: 0; }
.workflow-run-title strong { overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.workflow-run-title span, .workflow-run-meta span, .workflow-run-meta time { overflow: hidden; color: var(--color-text-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.workflow-run-meta { grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 5px 8px; }
.workflow-run-meta time { grid-column: 1 / -1; }
.workflow-status { width: max-content; padding-left: 7px; border-left: 2px solid var(--color-primary); color: var(--color-text-secondary) !important; }
.status-succeeded { border-color: var(--color-success); }.status-failed { border-color: var(--color-danger); }.status-cancelling, .status-cancelled { border-color: var(--color-warning); }
.workflow-progress { display: flex; align-items: center; gap: 7px; min-width: 0; }
.workflow-progress-track { flex: 1; height: 4px; overflow: hidden; background: var(--color-border-light); }
.workflow-progress-track span { display: block; height: 100%; background: var(--color-primary); transition: width .2s ease; }
.workflow-progress small { min-width: 29px; color: var(--color-text-muted); font-size: 10px; text-align: right; }
.workflow-empty, .workflow-error { padding: 13px 0 3px; color: var(--color-text-muted); font-size: 12px; }
.workflow-error, .workflow-detail-error { color: var(--color-danger); }
.workflow-run-detail { margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--color-border); }
.workflow-detail-heading strong { font-size: 13px; }.workflow-detail-heading span { margin-left: 7px; color: var(--color-text-muted); font-size: 11px; }
.workflow-events { display: grid; gap: 0; margin: 12px 0 0; padding: 0; list-style: none; }
.workflow-events li { display: grid; grid-template-columns: 14px minmax(0, 1fr); gap: 9px; padding-bottom: 11px; }
.event-dot { width: 6px; height: 6px; margin: 4px 0 0 3px; border: 1px solid var(--color-primary); border-radius: 50%; background: #fff; }
.workflow-events li:not(:last-child) .event-dot::after { display: block; width: 1px; height: 20px; margin: 6px 0 0 2px; background: var(--color-border); content: ''; }
.workflow-events li div { display: grid; gap: 2px; }.workflow-events strong { font-size: 12px; font-weight: 550; }.workflow-events span, .workflow-events time { color: var(--color-text-muted); font-size: 11px; }
.compact .workflow-runs-heading p { display: none; }.compact .workflow-run-main { grid-template-columns: minmax(130px, 1fr) minmax(100px, .8fr) minmax(85px, .6fr); }.compact .workflow-run-row { min-height: 50px; }
@media (max-width: 760px) { .workflow-run-main { grid-template-columns: minmax(0, 1fr) auto; gap: 7px 12px; }.workflow-run-meta { grid-column: 1; }.workflow-progress { grid-column: 2; grid-row: 1 / span 2; width: 90px; }.workflow-run-row { gap: 5px; } }
</style>
