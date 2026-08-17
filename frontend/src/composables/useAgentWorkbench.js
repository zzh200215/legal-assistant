import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { errorMessage } from '../api/errors'
import { useAgentExecution } from './useAgentExecution'
import { useAgentHistory } from './useAgentHistory'
import { useAgentApprovals } from './useAgentApprovals'
import { useAgentMeta } from './useAgentMeta'

// Agent 工作台视图（Agent.vue）共享状态门面：
// 按领域拆分为 执行编排（WS）/ 历史 / 审批 / 指标注册表 四个模块（useAgent*），
// 本文件仅做编排（路由参数、跨模块复位、导航）并保持与旧版完全一致的导出面，
// 子组件（AgentHeader / AgentCommandCenter / AgentExecutionView / AgentSidePanels）无需改动。
// 模块级单例：所有消费者共享同一份执行状态，避免双实例分叉。

let instance = null

export function useAgentWorkbench() {
  if (!instance) instance = createAgentWorkbench()
  return instance
}

function createAgentWorkbench() {
  const router = useRouter()

  const approvals = useAgentApprovals()
  const history = useAgentHistory()
  const meta = useAgentMeta()
  const execution = useAgentExecution({
    fetchApprovals: approvals.fetchApprovals,
    fetchHistory: history.fetchHistory,
    approvals: approvals.approvals,
  })

  const viewRun = async (row) => {
    try {
      const { data } = await api.getAgentRun(row.id)
      execution.bindRunData(data)
      execution.clearPlanPreview()
      execution.sensitiveApprovalVisible.value = false
      execution.activeApproval.value = null
      await approvals.fetchApprovals()
    } catch (error) {
      ElMessage.error(errorMessage(error) || '运行记录加载失败')
    }
  }

  const loadRunFromRoute = async (rawRunId) => {
    const runId = Number(rawRunId)
    if (!Number.isFinite(runId) || runId <= 0) return
    const row = history.history.value.find((item) => item.id === runId)
    if (row) {
      await viewRun(row)
      return
    }
    try {
      const { data } = await api.getAgentRun(runId)
      execution.bindRunData(data)
    } catch (error) {
      ElMessage.error(errorMessage(error) || '运行记录加载失败')
    }
  }

  const openDocument = (documentId) => {
    router.push({ path: '/documents', query: { documentId: String(documentId) } })
  }

  const openTask = (taskId) => {
    router.push({ path: '/tasks', query: { taskId: String(taskId), view: 'table' } })
  }

  const cancelCurrentRun = async () => {
    await execution.cancelCurrentRun({
      afterCancelled: async () => {
        await history.fetchHistory()
        await meta.fetchAgentMetrics()
      },
    })
  }

  const initialize = async ({ retryGoal, maxStepsQuery, runId } = {}) => {
    execution.applyDemoFromRoute()
    if (retryGoal) {
      execution.goal.value = String(retryGoal)
    }
    if (maxStepsQuery) {
      const parsed = Number(maxStepsQuery)
      if (Number.isFinite(parsed) && parsed >= 2 && parsed <= 10) {
        execution.maxSteps.value = parsed
      }
    }
    await approvals.fetchApprovals()
    await history.fetchHistory()
    await meta.fetchAgentMetrics()
    await meta.fetchAgentRegistry()
    await loadRunFromRoute(runId)
  }

  return {
    // execution
    goal: execution.goal,
    maxSteps: execution.maxSteps,
    loading: execution.loading,
    previewLoading: execution.previewLoading,
    runResult: execution.runResult,
    logs: execution.logs,
    cancelling: execution.cancelling,
    planPreview: execution.planPreview,
    planPreviewSignature: execution.planPreviewSignature,
    sensitiveApprovalVisible: execution.sensitiveApprovalVisible,
    activeApproval: execution.activeApproval,
    demoContext: execution.demoContext,
    finalAnswer: execution.finalAnswer,
    demoPreset: execution.demoPreset,
    artifactGroups: execution.artifactGroups,
    supervisorPlan: execution.supervisorPlan,
    hasArtifacts: execution.hasArtifacts,
    applyExample: execution.applyExample,
    closeAgentWs: execution.closeAgentWs,
    runAgentViaSocket: execution.runAgentViaSocket,
    syncQueryState: execution.syncQueryState,
    applyDemoFromRoute: execution.applyDemoFromRoute,
    clearPlanPreview: execution.clearPlanPreview,
    previewPlan: execution.previewPlan,
    run: execution.run,
    executeRun: execution.executeRun,
    openSensitiveApproval: execution.openSensitiveApproval,
    decideApproval: execution.decideApproval,
    cancelCurrentRun,
    // history
    history: history.history,
    historyTotal: history.historyTotal,
    historyLoading: history.historyLoading,
    historyError: history.historyError,
    historyPage: history.historyPage,
    historyPageSize: history.historyPageSize,
    fetchHistory: history.fetchHistory,
    handleHistoryPageChange: history.handleHistoryPageChange,
    viewRun,
    loadRunFromRoute,
    // approvals
    approvals: approvals.approvals,
    fetchApprovals: approvals.fetchApprovals,
    // meta
    agentMetrics: meta.agentMetrics,
    agentRegistry: meta.agentRegistry,
    supervisorRole: meta.supervisorRole,
    expertRoles: meta.expertRoles,
    fetchAgentMetrics: meta.fetchAgentMetrics,
    fetchAgentRegistry: meta.fetchAgentRegistry,
    // navigation
    openDocument,
    openTask,
    // init
    initialize,
  }
}
