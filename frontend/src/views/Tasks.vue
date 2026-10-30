<template>
  <section class="task-page">
    <!-- 方向 B 页头：28px 标题 + 说明，右侧放筛选（范围/视图）与主按钮（替代原“双 h1 + 下划线”版式） -->
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">任务</h1>
        <p class="app-page-sub">跟进案件中的待办、负责人和截止日期。</p>
      </div>
      <div class="app-page-head-actions">
        <button v-if="workflowCaseId" type="button" class="case-filter-chip" @click="clearCaseFilter">
          仅看案件 #{{ workflowCaseId }} ×
        </button>
        <el-select v-model="scopeFilter" class="scope-select" @change="handleScopeChange">
          <el-option v-for="item in scopeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-radio-group v-model="viewMode">
          <el-radio-button value="kanban">看板</el-radio-button>
          <el-radio-button value="table">表格</el-radio-button>
        </el-radio-group>
        <el-button type="primary" @click="createDialogs.openCreate()">新建任务</el-button>
      </div>
    </header>

    <!-- 统计：版式走 .app-stat；.task-brief 是 tasks-flow e2e 的断言锚点（断言“2 项任务”），保留 -->
    <div class="task-brief app-metric-grid-4" aria-label="任务摘要">
      <div class="app-stat">
        <span class="app-stat-label">任务总数</span>
        <div class="app-stat-value">{{ taskTotal || tasks.length }}<small> 项任务</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">进行中</span>
        <div class="app-stat-value">{{ inProgressCount }}<small> 项进行中</small></div>
      </div>
      <div class="app-stat" :class="{ 'is-alert': overdueCount }">
        <span class="app-stat-label">已逾期</span>
        <div class="app-stat-value">{{ overdueCount }}<small> 项逾期</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">共享任务</span>
        <div class="app-stat-value">{{ sharedTaskCount }}<small> 项共享</small></div>
      </div>
    </div>

    <WorkflowRunsPanel
      :case-id="workflowCaseId"
      :workflow-id="route.query.workflow_id"
      title="案件工作流"
      :description="workflowCaseId ? '当前案件的异步处理进度。' : '最近案件动作的异步处理进度。'"
      :limit="6"
      :compact="true"
    />

    <!-- 主体：工具栏 + 筛选 + 看板/列表收进一张 .app-card -->
    <section class="app-card task-list-card">
      <div class="app-card-head">
        <h2>任务清单</h2>
        <span class="app-card-head-meta">按状态推进案件协作事项</span>
        <details class="task-more-actions">
          <summary>更多创建方式</summary>
          <div class="task-more-menu">
            <button type="button" @click="createDialogs.openDocExtract()">从文档整理待办</button>
            <button type="button" @click="createDialogs.openChatExtract()">从对话整理待办</button>
          </div>
        </details>
      </div>
      <div class="app-card-body">
        <div v-if="sourceFilterMeta" class="source-filter-row app-chip-row">
          <span class="app-tag is-pending">来源筛选：{{ sourceFilterMeta.label }} #{{ sourceFilterMeta.sourceId }}</span>
          <el-button size="small" text type="info" @click="clearSourceFilter">清除来源筛选</el-button>
        </div>

        <QueryStateView :status="taskStatus" :error="taskError" :empty-text="taskEmptyText" min-height="240px" @retry="fetchTasks">
          <div v-if="viewMode === 'kanban'" class="kanban-board">
            <section v-for="col in columns" :key="col.status" class="kanban-column">
              <header class="kanban-column-head">
                <span class="kanban-column-title">{{ col.label }}</span>
                <span class="app-tag" :class="statusTagClass(col.status)">{{ getTasksByStatus(col.status).length }}</span>
              </header>
              <div class="kanban-column-body">
                <!-- .kanban-task 是 tasks-flow e2e 的点击锚点（.kanban-task 内文本含任务标题），保留 -->
                <div
                  v-for="task in getTasksByStatus(col.status)"
                  :key="task.id"
                  class="kanban-task app-thread"
                  @click="selectTask(task)"
                >
                  <span class="app-thread-title">{{ task.title }}</span>
                  <span class="app-thread-meta">
                    <span class="app-tag" :class="priorityTagClass(task.priority)">
                      {{ priorityLabelMap[task.priority] || task.priority }}
                    </span>
                    <span class="app-tag" :class="scopeTagClass(task)">{{ scopeLabel(task) }}</span>
                    <span v-if="task.assignee" class="app-tag is-accent">{{ task.assignee }}</span>
                    <span v-if="task.source_type" class="app-tag">
                      {{ sourceTypeLabelMap[task.source_type] || task.source_type }}
                    </span>
                    <span v-if="task.case_title" class="app-tag is-accent">{{ task.case_title }}</span>
                    <span v-if="task.due_date" class="kanban-task-due" :class="{ 'is-overdue': isTaskOverdue(task) }">
                      {{ dueLabel(task) }}
                    </span>
                  </span>
                  <span class="kanban-task-actions">
                    <el-button v-if="col.status !== 'in_progress'" size="small" type="primary" text @click.stop="changeStatus(task, 'in_progress')">开始</el-button>
                    <el-button v-if="col.status !== 'done'" size="small" type="success" text @click.stop="changeStatus(task, 'done')">完成</el-button>
                    <el-button v-if="col.status !== 'cancelled'" size="small" type="danger" text @click.stop="changeStatus(task, 'cancelled')">取消</el-button>
                  </span>
                </div>
                <p v-if="getTasksByStatus(col.status).length === 0" class="kanban-empty">
                  {{ taskEmptyText }}
                </p>
              </div>
            </section>
          </div>

          <el-table v-else v-loading="loading" :data="tasks" border :empty-text="taskEmptyText" class="task-table">
            <el-table-column prop="title" label="标题" />
            <el-table-column prop="description" label="事项说明" show-overflow-tooltip />
            <el-table-column prop="case_title" label="案件" width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ row.case_title || '—' }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <span class="app-tag" :class="statusTagClass(row.status)">{{ statusLabel(row.status) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="priority" label="优先级" width="80">
              <template #default="{ row }">
                <span class="app-tag" :class="priorityTagClass(row.priority)">
                  {{ priorityLabelMap[row.priority] || row.priority }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="范围" width="100">
              <template #default="{ row }">
                <span class="app-tag" :class="scopeTagClass(row)">{{ scopeLabel(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="assignee" label="负责人" width="100">
              <template #default="{ row }">{{ row.assignee || '-' }}</template>
            </el-table-column>
            <el-table-column prop="progress" label="进度" width="90">
              <template #default="{ row }">{{ row.progress || 0 }}%</template>
            </el-table-column>
            <el-table-column prop="source_type" label="来源" width="80">
              <template #default="{ row }">
                <span v-if="row.source_type" class="app-tag">
                  {{ sourceTypeLabelMap[row.source_type] || row.source_type }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button size="small" type="warning" text @click="decompose(row)">拆解</el-button>
                <el-button size="small" type="primary" text @click="selectTask(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </QueryStateView>
      </div>
    </section>

    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="taskPage"
      :page-size="taskPageSize"
      :total="taskTotal"
      class="app-pagination-end"
      @current-change="handleTaskPageChange"
    />

    <TaskDetailDialog
      ref="taskDetailRef"
      v-model="detailVisible"
      :task="selectedTask"
      :can-edit="canEditSelectedTask"
      @refresh="fetchTasks"
      @task-updated="handleTaskUpdated"
      @status-change="changeStatus"
      @decompose="decompose"
      @open-source="openSource"
      @open-agent-run="openAgentRun"
    />

    <TaskCreateDialogs ref="createDialogs" :loading="loading" @refresh="handleDialogsRefresh" />
  </section>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../api'
import { ElMessage } from 'element-plus/es/components/message/index'
import QueryStateView from '../components/QueryStateView.vue'
import { normalizeError } from '../api/errors.js'
import { getStatusLabel } from '../utils/status'
import { useAuthStore } from '../stores/auth'
import TaskCreateDialogs from '../components/tasks/TaskCreateDialogs.vue'
import TaskDetailDialog from '../components/tasks/TaskDetailDialog.vue'
import WorkflowRunsPanel from '../components/legal/WorkflowRunsPanel.vue'

const authStore = useAuthStore()
const createDialogs = ref(null)
const taskDetailRef = ref(null)
const route = useRoute()
const router = useRouter()
const currentUser = computed(() => authStore.user)
const tasks = ref([])
const taskPage = ref(1)
const taskPageSize = ref(12)
const taskTotal = ref(0)
const loading = ref(false)
// 三态（ux-audit M-13）：任务列表加载失败与"暂无任务"分离，失败可重试；看板/表格两视图共用
const taskError = ref(null)
const taskStatus = computed(() => {
  if (loading.value && !tasks.value.length) return 'loading'
  if (taskError.value) return 'error'
  if (!tasks.value.length) return 'empty'
  return 'success'
})
const viewMode = ref('kanban')
const scopeFilter = ref('all')
const detailVisible = ref(false)
const selectedTask = ref(null)
const workflowCaseId = computed(() => {
  const value = Number(route.query.case_id)
  return Number.isFinite(value) && value > 0 ? value : null
})

const priorityLabelMap = {
  high: '高',
  medium: '中',
  low: '低',
}

const sourceTypeLabelMap = {
  document: '文档',
  chat: '聊天',
  decompose: '拆解',
}

const columns = [
  { status: 'todo', label: '待办' },
  { status: 'in_progress', label: '进行中' },
  { status: 'done', label: '已完成' },
  { status: 'cancelled', label: '已取消' },
]

const scopeOptions = [
  { label: '全部可见', value: 'all' },
  { label: '我的', value: 'mine' },
  { label: '部门共享', value: 'department' },
  { label: '组织共享', value: 'organization' },
]

const canEditSelectedTask = computed(() => {
  if (!selectedTask.value || !currentUser.value) return false
  return selectedTask.value.user_id === currentUser.value.id
})

const sourceFilterMeta = computed(() => {
  const sourceType = typeof route.query.sourceType === 'string' ? route.query.sourceType : ''
  const sourceId = Number(route.query.sourceId)
  if (!sourceType || !Number.isFinite(sourceId) || sourceId <= 0) return null
  return {
    sourceType,
    sourceId,
    label: sourceTypeLabelMap[sourceType] || sourceType,
  }
})

const taskEmptyText = computed(() => {
  if (workflowCaseId.value) return `案件 #${workflowCaseId.value} 暂无任务`
  if (sourceFilterMeta.value) return `当前筛选下暂无来自${sourceFilterMeta.value.label} #${sourceFilterMeta.value.sourceId} 的任务`
  if (scopeFilter.value === 'mine') return '暂无我的任务'
  if (scopeFilter.value === 'department') return '暂无部门共享任务'
  if (scopeFilter.value === 'organization') return '暂无组织共享任务'
  return '暂无任务'
})

// 逾期口径只此一处，统计与看板卡片标记共用（有截止时间、未完成/未取消且已过期）
const isTaskOverdue = (item) => {
  if (!item?.due_date || item.status === 'done' || item.status === 'cancelled') return false
  const dueTime = new Date(item.due_date).getTime()
  return Number.isFinite(dueTime) && dueTime < Date.now()
}

const inProgressCount = computed(() => tasks.value.filter((item) => item.status === 'in_progress').length)
const overdueCount = computed(() => tasks.value.filter((item) => isTaskOverdue(item)).length)
const sharedTaskCount = computed(() => tasks.value.filter((item) => scopeLabel(item) !== '我的').length)

const getTasksByStatus = (status) => tasks.value.filter(t => t.status === status)

const scopeLabel = (item) => {
  if (!item || !currentUser.value) return '我的'
  if (item.user_id === currentUser.value.id) return '我的'
  if (item.department_id && currentUser.value.department_id && item.department_id === currentUser.value.department_id) return '部门共享'
  if (item.organization_id && currentUser.value.organization_id && item.organization_id === currentUser.value.organization_id) return '组织共享'
  return '共享'
}

// 方向 B 标签：状态/优先级/范围统一走 .app-tag 的语义变体（标签文案沿用 utils/status 口径）
const statusTagClass = (status) => {
  if (status === 'done') return 'is-ok'
  if (status === 'in_progress') return 'is-info'
  if (status === 'cancelled') return 'is-danger'
  return 'is-pending'
}

const statusLabel = (status) => getStatusLabel('task', status)

const priorityTagClass = (priority) => {
  if (priority === 'high') return 'is-danger'
  if (priority === 'medium') return 'is-pending'
  return ''
}

const scopeTagClass = (item) => {
  const label = scopeLabel(item)
  if (label === '部门共享') return 'is-pending'
  if (label === '组织共享') return 'is-ok'
  if (label === '共享') return 'is-info'
  return ''
}

const dueLabel = (item) => `截止 ${String(item?.due_date || '').slice(5, 10)}`

const replaceTaskQuery = (taskId) => {
  const nextQuery = { ...route.query, view: viewMode.value }
  if (taskId) {
    nextQuery.taskId = String(taskId)
  } else {
    delete nextQuery.taskId
  }
  router.replace({ query: nextQuery })
}

const openSource = (task) => {
  if (task.source_type === 'document' && task.source_id) {
    router.push({ path: '/documents', query: { documentId: String(task.source_id) } })
  }
}

const openAgentRun = (runId) => {
  router.push({ path: '/agent', query: { runId: String(runId) } })
}

const handleTaskUpdated = (task) => {
  if (task && selectedTask.value?.id === task.id) {
    selectedTask.value = task
  }
}

const fetchTasks = async () => {
  loading.value = true
  try {
    const { data } = await api.listTasks(null, {
      page: taskPage.value,
      page_size: taskPageSize.value,
      scope: scopeFilter.value,
      source_type: sourceFilterMeta.value?.sourceType || undefined,
      source_id: sourceFilterMeta.value?.sourceId || undefined,
      case_id: workflowCaseId.value || undefined,
    })
    tasks.value = data?.items || []
    taskTotal.value = data?.total || 0
  } catch (e) { taskError.value = normalizeError(e) }
  loading.value = false
}

const handleScopeChange = async () => {
  taskPage.value = 1
  await fetchTasks()
}

const clearSourceFilter = async () => {
  const nextQuery = { ...route.query }
  delete nextQuery.sourceType
  delete nextQuery.sourceId
  await router.replace({ query: nextQuery })
  taskPage.value = 1
  await fetchTasks()
}

const clearCaseFilter = async () => {
  const nextQuery = { ...route.query }
  delete nextQuery.case_id
  await router.replace({ query: nextQuery })
  taskPage.value = 1
  await fetchTasks()
}

const handleTaskPageChange = async (page) => {
  taskPage.value = page
  await fetchTasks()
}

const handleDialogsRefresh = (payload) => {
  if (payload?.resetPage) taskPage.value = 1
  fetchTasks()
}

const changeStatus = async (task, status) => {
  try {
    await api.updateTask(task.id, { status })
    ElMessage.success('状态已更新')
    fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '更新失败') }
}

const decompose = async (task) => {
  loading.value = true
  try {
    const { data } = await api.decomposeTask(task.id)
    ElMessage.success(`已拆解为 ${data.created_sub_tasks} 个子任务`)
    fetchTasks()
    if (selectedTask.value && selectedTask.value.id === task.id) {
      taskDetailRef.value?.reloadSubTasks(task.id)
    }
  } catch (e) { ElMessage.error(e.response?.data?.detail || '拆解失败') }
  loading.value = false
}

const selectTask = (task) => {
  selectedTask.value = task
  detailVisible.value = true
  replaceTaskQuery(task.id)
}


const applyTaskRoute = async (rawTaskId) => {
  const nextId = Number(rawTaskId)
  if (!Number.isFinite(nextId) || nextId <= 0) return

  let target = tasks.value.find((item) => item.id === nextId)
  if (!target) {
    try {
      const { data } = await api.getTask(nextId)
      tasks.value = [data, ...tasks.value.filter((item) => item.id !== data.id)]
      target = data
    } catch (e) {
      ElMessage.error(e.response?.data?.detail || '任务加载失败')
      return
    }
  }

  await selectTask(target)
}

onMounted(async () => {
  await authStore.loadMe()
  if (route.query.view === 'table' || route.query.taskId) {
    viewMode.value = 'table'
  }
  if (typeof route.query.scope === 'string' && scopeOptions.some(item => item.value === route.query.scope)) {
    scopeFilter.value = route.query.scope
  }
  await fetchTasks()
  await applyTaskRoute(route.query.taskId)
})

watch(viewMode, (value) => {
  const nextQuery = { ...route.query, view: value, scope: scopeFilter.value }
  router.replace({ query: nextQuery })
})

watch(scopeFilter, (value) => {
  router.replace({ query: { ...route.query, view: viewMode.value, scope: value } })
})

watch(
  () => route.query.taskId,
  async (value, oldValue) => {
    if (value === oldValue) return
    await applyTaskRoute(value)
  }
)

watch(
  () => route.query.case_id,
  async (value, oldValue) => {
    if (value === oldValue) return
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => route.query.scope,
  async (value, oldValue) => {
    if (value === oldValue || typeof value !== 'string' || !scopeOptions.some(item => item.value === value) || value === scopeFilter.value) return
    scopeFilter.value = value
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => [route.query.sourceType, route.query.sourceId],
  async (value, oldValue) => {
    if (JSON.stringify(value) === JSON.stringify(oldValue)) return
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(detailVisible, (visible) => {
  if (!visible) {
    selectedTask.value = null
    replaceTaskQuery(null)
  }
})
</script>

<style scoped>
/* 页面栅格：页头 / 统计 / 工作流 / 主体卡片 / 分页 之间只留一种间距 */
.task-page {
  display: grid;
  gap: var(--space-5);
}

.scope-select {
  width: 140px;
}

/* .task-brief 仅作为 e2e 断言锚点保留（tasks-flow 断言“2 项任务”），版式由 .app-metric-grid-4 提供 */
.task-brief {
  min-width: 0;
}

.app-stat.is-alert .app-stat-value {
  color: var(--color-danger);
}

.task-list-card {
  min-width: 0;
}

/* 卡片头右侧的“更多创建方式”：文字按钮 + 下拉菜单，不占按钮位 */
.task-more-actions {
  position: relative;
  color: var(--color-primary);
  font-size: var(--text-xs);
}
.task-more-actions summary {
  cursor: pointer;
  list-style: none;
}
.task-more-actions summary::-webkit-details-marker {
  display: none;
}
.task-more-menu {
  position: absolute;
  right: 0;
  top: 22px;
  z-index: 3;
  display: grid;
  min-width: 168px;
  padding: var(--space-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  box-shadow: var(--shadow-md);
}
.task-more-menu button {
  padding: 8px 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}
.task-more-menu button:hover {
  background: var(--color-surface-subtle);
  color: var(--color-primary);
}

.source-filter-row {
  margin-bottom: var(--space-4);
}

/* ─── 看板：外层一张卡，列是浅底泳道，任务卡复用 .app-thread ─── */
.kanban-board {
  display: flex;
  gap: var(--space-3);
  overflow-x: auto;
  padding-bottom: var(--space-1);
}

.kanban-column {
  display: grid;
  gap: var(--space-3);
  align-content: start;
  flex: 1 1 0;
  min-width: 240px;
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subtle);
}
.kanban-column-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.kanban-column-title {
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
}
.kanban-column-body {
  display: grid;
  gap: var(--space-2);
}

.kanban-task {
  gap: var(--space-2);
}
.kanban-task-actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: wrap;
}
.kanban-task-due {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.kanban-task-due.is-overdue {
  color: var(--color-danger);
  font-weight: 600;
}
.kanban-empty {
  margin: 0;
  padding: var(--space-4) 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  text-align: center;
}

/* ─── 表格视图：表格本身已由 element-overrides 统一，这里只保证网格项不撑破卡片 ─── */
.task-table {
  width: 100%;
  min-width: 0;
}

/* 页面栅格已给出与上方的间距，去掉工具类的额外上边距 */
.app-pagination-end {
  margin-top: 0;
}

.case-filter-chip {
  padding: 5px 10px;
  border: 1px solid var(--color-primary-light);
  border-radius: var(--radius-full);
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 500;
  cursor: pointer;
}

.case-filter-chip:hover {
  border-color: var(--color-primary);
}

@media (max-width: 1100px) {
  .app-page-head-actions {
    width: 100%;
    margin-left: 0;
  }
}
@media (max-width: 760px) {
  .kanban-column {
    min-width: 220px;
  }
}
</style>
