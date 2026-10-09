<template>
  <div class="task-page">
    <header class="task-heading">
      <div>
        <p class="section-eyebrow">案件协作</p>
        <h1>任务</h1>
        <p>跟进案件中的待办、负责人和截止日期。</p>
      </div>
      <el-button type="primary" @click="createDialogs.openCreate()">新建任务</el-button>
    </header>

    <div class="task-brief" aria-label="任务摘要">
      <span><strong>{{ taskTotal || tasks.length }}</strong> 项任务</span>
      <span><strong>{{ inProgressCount }}</strong> 项进行中</span>
      <span :class="{ 'brief-alert': overdueCount }"><strong>{{ overdueCount }}</strong> 项逾期</span>
      <span><strong>{{ sharedTaskCount }}</strong> 项共享</span>
    </div>

    <el-card class="toolbar-card">
      <template #header>
        <div class="section-header">
          <div><span>任务清单</span><small>按状态推进案件协作事项</small></div>
        </div>
      </template>
      <el-space wrap class="toolbar-actions">
        <el-select v-model="scopeFilter" style="width: 140px" @change="handleScopeChange">
          <el-option v-for="item in scopeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-radio-group v-model="viewMode" style="margin-left: 16px">
          <el-radio-button value="kanban">看板</el-radio-button>
          <el-radio-button value="table">表格</el-radio-button>
        </el-radio-group>
        <details class="task-more-actions">
          <summary>更多创建方式</summary>
          <div class="task-more-menu">
            <button type="button" @click="createDialogs.openDocExtract()">从文档整理待办</button>
            <button type="button" @click="createDialogs.openChatExtract()">从对话整理待办</button>
          </div>
        </details>
      </el-space>
      <div v-if="sourceFilterMeta" class="source-filter-row">
        <el-tag size="small" type="warning">来源筛选：{{ sourceFilterMeta.label }} #{{ sourceFilterMeta.sourceId }}</el-tag>
        <el-button size="small" text type="info" @click="clearSourceFilter">清除来源筛选</el-button>
      </div>
    </el-card>

    <WorkflowRunsPanel
      :case-id="workflowCaseId"
      :workflow-id="route.query.workflow_id"
      title="案件工作流"
      :description="workflowCaseId ? '当前案件的异步处理进度。' : '最近案件动作的异步处理进度。'"
      :limit="6"
      :compact="true"
    />

    <div v-if="viewMode === 'kanban'" class="kanban-board">
      <el-card v-for="col in columns" :key="col.status" class="kanban-column">
        <template #header>
          <div class="kanban-column-header">
            <span>{{ col.label }}</span>
            <el-tag size="small" round>{{ getTasksByStatus(col.status).length }}</el-tag>
          </div>
        </template>
        <div
          v-for="task in getTasksByStatus(col.status)"
          :key="task.id"
          class="kanban-task"
          @click="selectTask(task)"
        >
          <div class="kanban-task-title">{{ task.title }}</div>
          <div class="kanban-task-meta">
            <el-tag size="small" :type="task.priority === 'high' ? 'danger' : task.priority === 'medium' ? 'warning' : 'info'">
              {{ priorityLabelMap[task.priority] || task.priority }}
            </el-tag>
            <el-tag size="small" :type="scopeTagType(task)">{{ scopeLabel(task) }}</el-tag>
            <el-tag v-if="task.assignee" size="small" type="success">
              {{ task.assignee }}
            </el-tag>
            <el-tag v-if="task.source_type" size="small" type="info">
              {{ sourceTypeLabelMap[task.source_type] || task.source_type }}
            </el-tag>
          </div>
          <div class="kanban-task-actions">
            <el-button v-if="col.status !== 'in_progress'" size="small" type="primary" text @click.stop="changeStatus(task, 'in_progress')">开始</el-button>
            <el-button v-if="col.status !== 'done'" size="small" type="success" text @click.stop="changeStatus(task, 'done')">完成</el-button>
            <el-button v-if="col.status !== 'cancelled'" size="small" type="danger" text @click.stop="changeStatus(task, 'cancelled')">取消</el-button>
          </div>
        </div>
        <div v-if="getTasksByStatus(col.status).length === 0" class="kanban-empty">
          {{ taskEmptyText }}
        </div>
      </el-card>
    </div>

    <el-card v-else class="table-card">
      <el-table :data="tasks" v-loading="loading" border :empty-text="taskEmptyText">
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="description" label="事项说明" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <StatusTag kind="task" :status="row.status" size="small" />
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80">
          <template #default="{ row }">
            <el-tag :type="row.priority === 'high' ? 'danger' : row.priority === 'medium' ? 'warning' : 'info'" size="small">
              {{ priorityLabelMap[row.priority] || row.priority }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="范围" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="scopeTagType(row)">{{ scopeLabel(row) }}</el-tag>
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
            <el-tag v-if="row.source_type" size="small" type="info">
              {{ sourceTypeLabelMap[row.source_type] || row.source_type }}
            </el-tag>
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
    </el-card>

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
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElSpace } from 'element-plus/es/components/space/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/space/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'
import { ElMessage } from 'element-plus/es/components/message/index'
import StatusTag from '../components/StatusTag.vue'
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
  if (sourceFilterMeta.value) return `当前筛选下暂无来自${sourceFilterMeta.value.label} #${sourceFilterMeta.value.sourceId} 的任务`
  if (scopeFilter.value === 'mine') return '暂无我的任务'
  if (scopeFilter.value === 'department') return '暂无部门共享任务'
  if (scopeFilter.value === 'organization') return '暂无组织共享任务'
  return '暂无任务'
})

const inProgressCount = computed(() => tasks.value.filter((item) => item.status === 'in_progress').length)
const overdueCount = computed(() => {
  const now = Date.now()
  return tasks.value.filter((item) => {
    if (!item?.due_date || item.status === 'done' || item.status === 'cancelled') return false
    const dueTime = new Date(item.due_date).getTime()
    return Number.isFinite(dueTime) && dueTime < now
  }).length
})
const sharedTaskCount = computed(() => tasks.value.filter((item) => scopeLabel(item) !== '我的').length)

const getTasksByStatus = (status) => tasks.value.filter(t => t.status === status)

const scopeLabel = (item) => {
  if (!item || !currentUser.value) return '我的'
  if (item.user_id === currentUser.value.id) return '我的'
  if (item.department_id && currentUser.value.department_id && item.department_id === currentUser.value.department_id) return '部门共享'
  if (item.organization_id && currentUser.value.organization_id && item.organization_id === currentUser.value.organization_id) return '组织共享'
  return '共享'
}

const scopeTagType = (item) => {
  const label = scopeLabel(item)
  if (label === '我的') return 'info'
  if (label === '部门共享') return 'warning'
  if (label === '组织共享') return 'success'
  return ''
}

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
    })
    tasks.value = data?.items || []
    taskTotal.value = data?.total || 0
  } catch (e) { ElMessage.error('获取任务失败') }
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
.task-page {
  display: grid;
  gap: var(--space-6);
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.task-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding-bottom: 20px; border-bottom: 1px solid var(--color-border); }
.task-heading h1 { margin: 0; color: var(--color-text); font-size: 30px; font-weight: 620; letter-spacing: 0; }
.task-heading p:last-child { margin: 7px 0 0; color: var(--color-text-secondary); font-size: 14px; }
.task-brief { display: flex; align-items: center; gap: 24px; min-height: 42px; padding: 0 4px; border-bottom: 1px solid var(--color-border); color: var(--color-text-secondary); font-size: var(--text-sm); }
.task-brief span { padding-right: 24px; border-right: 1px solid var(--color-border-light); }
.task-brief span:last-child { border-right: 0; }
.task-brief strong { margin-right: 4px; color: var(--color-text); font-size: var(--text-lg); font-weight: 600; }
.task-brief .brief-alert strong { color: var(--color-danger); }

.toolbar-card,
.table-card {
  border-radius: var(--radius-lg);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
}
.section-header small { margin-left: 10px; color: var(--color-text-muted); font-size: var(--text-xs); }

.toolbar-actions {
  width: 100%;
}

.source-filter-row {
  margin-top: var(--space-3);
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.task-more-actions { position: relative; margin-left: auto; color: var(--color-primary); font-size: 12px; }
.task-more-actions summary { cursor: pointer; list-style: none; }
.task-more-actions summary::-webkit-details-marker { display: none; }
.task-more-menu { position: absolute; right: 0; top: 24px; z-index: 3; display: grid; min-width: 160px; padding: 6px; border: 1px solid var(--color-border); background: var(--color-surface); box-shadow: var(--shadow-sm); }
.task-more-menu button { padding: 8px 10px; border: 0; background: transparent; color: var(--color-text-secondary); font: inherit; font-size: 12px; text-align: left; cursor: pointer; }
.task-more-menu button:hover { background: var(--color-bg-alt); color: var(--color-primary); }

/* ─── Kanban ─── */
.kanban-board {
  display: flex;
  gap: var(--space-4);
  overflow-x: auto;
  padding-bottom: 4px;
}

.kanban-column {
  min-width: 300px;
  flex: 1;
  border-radius: var(--radius-lg);
}
.kanban-column-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.kanban-column :deep(.el-card__body) {
  padding: var(--space-3);
}

.kanban-task {
  padding: var(--space-3);
  margin-bottom: var(--space-2);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
  display: grid;
  gap: var(--space-2);
  transition: all var(--transition-fast);
}
.kanban-task:hover {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  box-shadow: var(--shadow-sm);
}
.kanban-task:active {
  transform: translateY(0);
}
.kanban-task-title {
  font-weight: 600;
  color: var(--color-text);
}
.kanban-task-meta,
.kanban-task-actions {
  display: flex;
  gap: var(--space-1);
  align-items: center;
  flex-wrap: wrap;
}
.kanban-empty {
  color: var(--color-text-muted);
  text-align: center;
  padding: var(--space-6) var(--space-4);
}

/* ─── Detail ─── */
.detail-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}
.detail-metric {
  padding: var(--space-4) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border-light);
  display: grid;
  gap: var(--space-1);
}
.detail-metric span,
.inline-hint,
.comment-meta,
.agent-run-meta,
.readonly-note,
.dialog-copy {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.detail-metric strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}

.detail-descriptions,
.detail-table {
  margin-top: var(--space-2);
}
.detail-section {
  margin-top: var(--space-5);
}
.detail-section h4 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
  color: var(--color-text);
}

.detail-action-row,
.editor-row,
.agent-run-top,
.form-inline {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.editor-row-top {
  align-items: flex-start;
}

.comment-list,
.agent-run-list,
.form-grid {
  display: grid;
  gap: var(--space-3);
}
.comment-item,
.agent-run-item {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
}
.comment-content {
  margin-top: var(--space-1);
  white-space: pre-wrap;
  color: var(--color-text);
}

.task-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
.task-dialog :deep(.el-dialog__body) {
  padding-top: var(--space-2);
}

@media (max-width: 1100px) {
  .task-heading { align-items: flex-start; flex-direction: column; }
}
@media (max-width: 760px) {
  .task-brief { align-items: flex-start; flex-wrap: wrap; gap: 10px 16px; padding: 8px 4px; }
  .task-brief span { padding-right: 16px; }
  .detail-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
