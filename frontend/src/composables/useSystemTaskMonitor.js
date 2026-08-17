import { computed, ref } from 'vue'

// 任务中心为模块级单例：顶部概览条（运行/失败/重试/成功率）与「任务中心」tab 共享同一份
// taskRuns 状态，避免拆分成两个组件后各自持有副本导致顶部统计与列表割裂。
const taskDays = ref(30)
const taskScope = ref('mine')
const taskSource = ref(null)
const taskStatus = ref(null)
const taskRuns = ref([])
const taskLoading = ref(false)
const taskRunPage = ref(1)
const taskRunPageSize = ref(20)
const taskRunTotal = ref(0)
const retryingTaskKey = ref('')
const taskDetailVisible = ref(false)
const selectedTaskDetail = ref(null)

const runningTaskCount = computed(() => taskRuns.value.filter((item) => item.status === 'running').length)
const failedTaskCount = computed(() => taskRuns.value.filter((item) => item.status === 'failed').length)
const retryableTaskCount = computed(() => taskRuns.value.filter((item) => item.retryable).length)
const agentRunCount = computed(() => taskRuns.value.filter((item) => item.source === 'agent').length)
const agentSucceededCount = computed(() => taskRuns.value.filter((item) => item.source === 'agent' && item.status === 'succeeded').length)
const agentSuccessRate = computed(() => agentRunCount.value ? agentSucceededCount.value / agentRunCount.value : 0)

let retriedCallback = () => {}

// Owns task-centre data, retry behaviour and navigation from the system page.
export function useSystemTaskMonitor({ client, message, router, isAdmin, onTaskRetried }) {
  if (onTaskRetried) retriedCallback = onTaskRetried
  const fetchTaskRuns = async () => {
    taskLoading.value = true
    try {
      const params = { days: taskDays.value, page: taskRunPage.value, page_size: taskRunPageSize.value, scope: isAdmin.value ? taskScope.value : 'mine' }
      if (taskSource.value) params.source = taskSource.value
      if (taskStatus.value) params.status = taskStatus.value
      const { data } = await client.listTaskRuns(params)
      taskRuns.value = data?.items || []; taskRunTotal.value = data?.total || 0
    } catch (error) {
      taskRuns.value = []; taskRunTotal.value = 0
      if (error.response?.status === 403) { message.warning(error.response?.data?.detail || '当前账号无权查看该范围任务'); taskScope.value = 'mine' }
      else message.error(error.response?.data?.detail || '获取任务中心失败')
    } finally { taskLoading.value = false }
  }
  const resetTaskRunPagination = async () => { taskRunPage.value = 1; await fetchTaskRuns() }
  const handleTaskRunPageChange = async (page) => { taskRunPage.value = page; await fetchTaskRuns() }
  const openTaskTarget = (row) => {
    const routes = { document: '/documents', task: '/tasks' }
    if (row.source === 'agent' || row.target_type === 'agent_run') return router.push({ path: '/agent', query: { runId: String(row.target_id) } })
    if (row.target_type === 'connector_sync_job' && row.target_id) return router.push({ path: '/system', query: { tab: 'connectors' } })
    if (routes[row.target_type] && row.target_id) {
      const key = { document: 'documentId', task: 'taskId' }[row.target_type]
      return router.push({ path: routes[row.target_type], query: { [key]: String(row.target_id), ...(row.target_type === 'task' ? { view: 'table' } : {}) } })
    }
  }
  const retryTask = async (row) => {
    retryingTaskKey.value = `${row.source}:${row.task_key}`
    try {
      const { data } = await client.retryTaskRun({ source: row.source, task_key: row.task_key })
      if (row.source === 'agent') { router.push({ path: '/agent', query: { retryGoal: data.goal, maxSteps: String(data.max_steps || 5) } }); message.success('已带入 Agent 重试参数') }
      else { message.success('任务已重新提交'); await fetchTaskRuns(); retriedCallback() }
    } catch (error) { message.error(error.response?.data?.detail || '重试失败') } finally { retryingTaskKey.value = '' }
  }
  const showTaskDetail = (row) => { selectedTaskDetail.value = row; taskDetailVisible.value = true }
  return { taskDays, taskScope, taskSource, taskStatus, taskRuns, taskLoading, taskRunPage, taskRunPageSize, taskRunTotal, retryingTaskKey, taskDetailVisible, selectedTaskDetail, runningTaskCount, failedTaskCount, retryableTaskCount, agentRunCount, agentSucceededCount, agentSuccessRate, fetchTaskRuns, resetTaskRunPagination, handleTaskRunPageChange, openTaskTarget, retryTask, showTaskDetail }
}
