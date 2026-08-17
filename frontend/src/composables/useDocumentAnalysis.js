import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAsyncJob, toCeleryState } from './useAsyncJob.js'
import { useMutation } from '../query/useMutation.js'
import { qkPrefix } from '../query/keys'
import { errorMessage } from '../api/errors'
import api from '../api'

// 文档「分析长任务」领域模块：
//  - 复用统一 Job 状态管理（WS 优先 / 指数退避轮询降级，useAsyncJob）
//  - 已存在分析任务（排队/执行中）恢复观察；已完成任务直接取结果；否则提交新分析
//  - 结果归一化（summary/risks/todos/clauses/structured_fields/references），失败清空并提示

const emptyAnalysis = () => ({
  summary: '',
  risks: [],
  todos: [],
  clauses: [],
  structured_fields: { dates: [], amounts: [], owners: [], risk_clauses: [] },
  references: [],
})

const normalizeStructuredFields = (value) => ({
  dates: value?.dates || [],
  amounts: value?.amounts || [],
  owners: value?.owners || [],
  risk_clauses: value?.risk_clauses || [],
})

function normalizeResult(result) {
  return {
    summary: result?.summary || '',
    risks: result?.risks || [],
    todos: result?.todos || [],
    clauses: result?.clauses || [],
    structured_fields: normalizeStructuredFields(result?.structured_fields),
    references: result?.references || [],
  }
}

export function useDocumentAnalysis({ docId, parseJobs, onCompleted }) {
  const analysis = ref(emptyAnalysis())
  const loading = ref(false)
  const taskId = ref(null)
  const taskMessage = ref('')

  // 统一 Job 状态：轮询拉取 /documents/task/{task_id}/status（Celery 状态归一化）
  const job = useAsyncJob({
    jobId: taskId,
    fetchStatus: (id) => api.getDocumentTaskStatus(id),
    enabled: () => taskId.value != null,
    poll: { base: 1500, max: 30000, factor: 2 },
    onTerminal: (status, payload) => {
      if (status === 'succeeded') {
        analysis.value = normalizeResult(payload?.result)
        taskMessage.value = '文档分析已完成'
        if (onCompleted) onCompleted()
      } else if (status === 'failed') {
        analysis.value = emptyAnalysis()
        taskMessage.value = payload?.error || payload?.message || '文档分析失败'
        ElMessage.error(taskMessage.value)
        if (onCompleted) onCompleted()
      } else {
        taskMessage.value = status === 'cancelled' ? '文档分析已取消' : '文档分析已结束'
        if (onCompleted) onCompleted()
      }
    },
  })

  const analysisTask = computed(() => ({
    taskId: taskId.value,
    state: taskId.value == null ? '' : toCeleryState(job.status.value),
    message: taskMessage.value,
  }))
  const analysisTaskMessage = computed(() => taskMessage.value || '文档分析正在后台执行')

  watch(job.status, (value) => {
    if (value === 'running' || value === 'queued' || value === 'retrying') {
      taskMessage.value = '文档分析正在后台执行'
    }
  })

  const reset = () => {
    job.reset()
    taskId.value = null
    taskMessage.value = ''
    loading.value = false
    analysis.value = emptyAnalysis()
  }

  const clearAnalysisPolling = () => {
    job.stop()
  }

  const submitMutation = useMutation({
    mutationFn: (payload, ctx) => api.analyzeDocument(payload.docId, 500, true, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('documents', 'parse-jobs')],
    onError: (error) => {
      analysis.value = emptyAnalysis()
      ElMessage.error(error.message || '文档分析失败')
    },
  })

  async function runAnalysis() {
    if (!docId.value) return
    const jobs = parseJobs.value
    const existingJob = jobs.find((j) => j.job_type === 'document_analysis')
    if (existingJob) {
      if (existingJob.status === 'completed' && existingJob.task_id) {
        loading.value = true
        try {
          const { data } = await api.getDocumentTaskStatus(existingJob.task_id)
          if (data.result) analysis.value = normalizeResult(data.result)
        } catch {
          // 结果拉取失败静默，不影响其他功能
        } finally {
          loading.value = false
        }
        return
      }
      if ((existingJob.status === 'pending' || existingJob.status === 'running') && existingJob.task_id) {
        taskMessage.value = existingJob.message || '文档分析任务进行中'
        taskId.value = existingJob.task_id
        job.refresh()
        return
      }
    }
    loading.value = true
    try {
      const { data } = await submitMutation.mutate({ docId: docId.value })
      if (data.async_mode && data.task_id) {
        analysis.value = emptyAnalysis()
        taskMessage.value = '文档分析任务已提交'
        taskId.value = data.task_id
      } else {
        analysis.value = normalizeResult(data)
      }
    } finally {
      loading.value = false
    }
  }

  const retryParse = async () => {
    if (!docId.value) return
    try {
      await api.retryDocumentParse(docId.value, { idempotencyKey: `retry-parse-${docId.value}` })
      ElMessage.success('已提交解析重试任务')
      if (onCompleted) onCompleted()
    } catch (error) {
      ElMessage.error(errorMessage(error) || '重试解析失败')
    }
  }

  return {
    analysis,
    loading,
    analysisTask,
    analysisTaskMessage,
    taskId,
    runAnalysis,
    retryParse,
    reset,
    clearAnalysisPolling,
  }
}
