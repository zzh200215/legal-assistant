import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useMutation } from '../query/useMutation.js'
import { errorMessage } from '../api/errors'
import api from '../api'
import { AGENT_DEMO_PRESETS, buildDocumentRiskGoal, getAgentDemoPreset } from '../utils/agentDemo'
import { createWsClient } from '../utils/wsClient'

// Agent「执行编排」领域模块：目标输入、计划预览、WS 实时执行（P1 协议）、取消、敏感审批弹窗。
// WS 客户端升级为协议合规 wsClient（welcome/ack/ping/subscribe/resume/断线重连），
// 旧事件语义（run_started/step_* /run_completed/run_snapshot/error）保持兼容。

export function agentSocketUrl(locationRef = window.location) {
  const protocol = locationRef.protocol === 'https:' ? 'wss' : 'ws'
  const host = import.meta.env.DEV ? (import.meta.env.VITE_WS_HOST || 'localhost:8001') : locationRef.host
  return `${protocol}://${host}/api/ws/agent`
}

export function useAgentExecution({ fetchApprovals, fetchHistory, approvals }) {
  const route = useRoute()
  const router = useRouter()

  const goal = ref('')
  const maxSteps = ref(5)
  const loading = ref(false)
  const previewLoading = ref(false)
  const runResult = ref(null)
  const logs = ref([])
  const cancelling = ref(false)
  const planPreview = ref(null)
  const planPreviewSignature = ref('')
  const sensitiveApprovalVisible = ref(false)
  const activeApproval = ref(null)
  const demoContext = ref({
    type: '',
    documentId: null,
    documentTitle: '',
  })

  const finalAnswer = computed(() => runResult.value?.final_answer || runResult.value?.result || '')
  const demoPreset = computed(() => getAgentDemoPreset(demoContext.value.type, demoContext.value))
  const artifactGroups = computed(() => {
    const a = runResult.value?.artifacts || {}
    return {
      documents: Array.isArray(a.documents) ? a.documents : [],
      tasks: Array.isArray(a.tasks) ? a.tasks : [],
    }
  })
  const supervisorPlan = computed(() => runResult.value?.supervisor_plan || {})
  const hasArtifacts = computed(() =>
    ['documents', 'tasks'].some((key) => (artifactGroups.value[key] || []).length)
  )

  let agentWsClient = null

  function bindRunData(data) {
    runResult.value = data
    logs.value = data.logs || []
  }

  function closeAgentWs() {
    if (!agentWsClient) return
    try {
      agentWsClient.close(1000)
    } catch {
      // 连接已关闭时静默
    }
    agentWsClient = null
  }

  const applyExample = (value) => {
    goal.value = value
    planPreview.value = null
    planPreviewSignature.value = ''
    sensitiveApprovalVisible.value = false
    activeApproval.value = null
    demoContext.value = {
      type: '',
      documentId: null,
      documentTitle: '',
    }
  }

  const clearPlanPreview = () => {
    planPreview.value = null
    planPreviewSignature.value = ''
  }

  const currentPlanSignature = () => `${goal.value.trim()}::${maxSteps.value}`

  const applyDemoFromRoute = () => {
    const demo = String(route.query.demo || '')
    if (demo !== 'document_risk') {
      demoContext.value = {
        type: '',
        documentId: null,
        documentTitle: '',
      }
      return
    }
    const documentId = Number(route.query.documentId)
    const documentTitle = String(route.query.documentTitle || '')
    if (!Number.isFinite(documentId) || documentId <= 0) return
    demoContext.value = {
      type: demo,
      documentId,
      documentTitle,
    }
    goal.value = buildDocumentRiskGoal(documentId)
    maxSteps.value = AGENT_DEMO_PRESETS.document_risk.maxSteps
  }

  const syncQueryState = () => {
    const updates = { ...route.query }
    if (goal.value?.trim()) {
      updates.retryGoal = goal.value.trim()
    }
    if (maxSteps.value) {
      updates.maxSteps = String(maxSteps.value)
    }
    router.replace({ query: updates })
  }

  async function handleWsEvent(data, resolve) {
    if (!data || typeof data !== 'object') return
    const type = data.type
    if (type === 'run_started') {
      runResult.value = {
        run_id: data.run_id,
        goal: data.goal,
        status: data.status,
        created_at: data.created_at,
      }
      return
    }
    if (type === 'run_resumed') {
      if (runResult.value) {
        runResult.value = {
          ...runResult.value,
          run_id: data.run_id,
          status: 'running',
        }
      }
      return
    }
    if (type === 'step_started') {
      logs.value = [
        ...logs.value.filter((item) => !(String(item.id).startsWith('pending-') && item.step === data.step)),
        {
          id: `pending-${data.step}`,
          agent_run_id: runResult.value?.run_id,
          step: data.step,
          action_type: data.action_type,
          thought: data.thought,
          tool_name: data.tool_name,
          input_params: data.input_params ? JSON.stringify(data.input_params, null, 2) : '',
          raw_decision: null,
          observation: null,
          output_result: null,
          status: 'pending',
          error: null,
          duration_ms: null,
          created_at: new Date().toISOString(),
        },
      ].sort((a, b) => (a.step || 0) - (b.step || 0))
      return
    }
    if (type === 'step_completed') {
      const nextLog = data.log
      logs.value = [
        ...logs.value.filter((item) => !(String(item.id).startsWith('pending-') && item.step === nextLog.step)),
        nextLog,
      ].sort((a, b) => (a.step || 0) - (b.step || 0))
      return
    }
    if (type === 'run_completed' || type === 'run_failed' || type === 'run_waiting_approval' || type === 'cancelled') {
      runResult.value = {
        ...runResult.value,
        ...(type === 'cancelled' ? { status: data.cancelled === false ? 'running' : 'cancelled' } : data.run),
        logs: logs.value,
      }
      loading.value = false
      if (type === 'run_completed') ElMessage.success('Agent 执行完成')
      if (type === 'run_waiting_approval') {
        ElMessage.warning('检测到敏感操作，请确认后继续执行')
        // 先刷新审批列表再打开确认弹窗（弹窗按 id 从列表取对象）
        if (fetchApprovals) await fetchApprovals()
        openSensitiveApproval(data.approval_request_id)
      }
      if (type === 'cancelled' && data.cancelled !== false) ElMessage.info('执行已取消')
      if (fetchHistory) fetchHistory()
      resolve()
      return
    }
    if (type === 'run_snapshot') {
      bindRunData({
        ...data.run,
        logs: data.logs,
      })
      loading.value = false
      resolve()
      return
    }
    if (type === 'error') {
      loading.value = false
      ElMessage.error(data.message || '执行失败')
      resolve()
    }
  }

  /** 通过 WS（P1 协议）发起执行；终态（完成/失败/审批/快照/取消/错误）时 resolve */
  function runAgentViaSocket(payload, { resetResult = true } = {}) {
    loading.value = true
    if (resetResult) {
      runResult.value = null
      logs.value = []
    }
    closeAgentWs()

    return new Promise((resolve) => {
      const token = localStorage.getItem('token')
      if (!token) {
        loading.value = false
        ElMessage.error('登录状态已失效')
        resolve()
        return
      }
      try {
        agentWsClient = createWsClient({
          url: agentSocketUrl(),
          channels: ['agent'],
          onEvent: (data) => handleWsEvent(data, resolve),
          onStatus: (status) => {
            if (status === 'error') {
              loading.value = false
              ElMessage.error('Agent 实时连接异常')
              resolve()
            }
          },
        })
        agentWsClient.connect()
        // 发送兼容旧协议的载荷（无 type 字段）：后端 ws_api 对旧格式消息保持兼容
        agentWsClient.send(payload)
      } catch (error) {
        loading.value = false
        ElMessage.error(errorMessage(error) || 'Agent 实时连接异常')
        resolve()
      }
    })
  }

  const previewMutation = useMutation({
    mutationFn: (payload, ctx) => api.previewAgentPlan(payload.goal, payload.maxSteps, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result) => {
      planPreview.value = result.data
      planPreviewSignature.value = currentPlanSignature()
    },
    onError: (error) => {
      clearPlanPreview()
      ElMessage.error(error.message || '计划预览失败')
    },
  })

  const previewPlan = async () => {
    if (!goal.value.trim()) {
      ElMessage.warning('请输入目标')
      return
    }
    previewLoading.value = true
    try {
      await previewMutation.mutate({ goal: goal.value.trim(), maxSteps: maxSteps.value })
    } finally {
      previewLoading.value = false
    }
  }

  const executeRun = async () => {
    if (!goal.value.trim()) {
      ElMessage.warning('请输入目标')
      return
    }
    await runAgentViaSocket({
      action: 'run',
      goal: goal.value,
      max_steps: maxSteps.value,
    })
  }

  const run = executeRun

  const openSensitiveApproval = (approvalOrId) => {
    const approval = typeof approvalOrId === 'object' && approvalOrId !== null
      ? approvalOrId
      : approvals?.value?.find((item) => item.id === Number(approvalOrId))
    if (!approval || approval.status !== 'pending') return
    activeApproval.value = approval
    sensitiveApprovalVisible.value = true
  }

  const rejectMutation = useMutation({
    mutationFn: (payload, ctx) => api.decideApproval(payload.id, payload.body, { idempotencyKey: ctx.idempotencyKey }),
  })

  const decideApproval = async (item, approved) => {
    try {
      if (approved) {
        sensitiveApprovalVisible.value = false
        await runAgentViaSocket(
          {
            action: 'resume_approval',
            approval_id: item.id,
          },
          { resetResult: false }
        )
      } else {
        await rejectMutation.mutate({
          id: item.id,
          body: { approved: false, decision_note: '用户拒绝敏感操作' },
        })
        if (activeApproval.value?.id === item.id) {
          sensitiveApprovalVisible.value = false
          activeApproval.value = null
        }
        ElMessage.success('审批已拒绝')
      }
      if (fetchApprovals) await fetchApprovals()
      if (fetchHistory) await fetchHistory()
    } catch (error) {
      ElMessage.error(errorMessage(error) || '审批失败')
    }
  }

  const cancelMutation = useMutation({
    mutationFn: (payload, ctx) => api.cancelAgentRun(payload.runId, payload.reason, { idempotencyKey: ctx.idempotencyKey }),
    onError: (error) => {
      ElMessage.error(error.message || '取消执行失败')
    },
  })

  const cancelCurrentRun = async ({ afterCancelled } = {}) => {
    const runId = runResult.value?.id || runResult.value?.run_id
    if (!runId) return
    cancelling.value = true
    try {
      const { data } = await cancelMutation.mutate({ runId, reason: '用户在 Agent 工作台取消执行' })
      runResult.value = { ...runResult.value, ...data }
      ElMessage.success(data.status === 'cancelled' ? '执行已取消' : '已请求取消，将在当前步骤结束后停止')
      if (afterCancelled) await afterCancelled()
    } finally {
      cancelling.value = false
    }
  }

  return {
    goal,
    maxSteps,
    loading,
    previewLoading,
    runResult,
    logs,
    cancelling,
    planPreview,
    planPreviewSignature,
    sensitiveApprovalVisible,
    activeApproval,
    demoContext,
    finalAnswer,
    demoPreset,
    artifactGroups,
    supervisorPlan,
    hasArtifacts,
    bindRunData,
    closeAgentWs,
    runAgentViaSocket,
    applyExample,
    clearPlanPreview,
    applyDemoFromRoute,
    syncQueryState,
    previewPlan,
    run,
    executeRun,
    openSensitiveApproval,
    decideApproval,
    cancelCurrentRun,
  }
}
