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
          仅看案件 {{ caseLabel }} ×
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

    <!-- 统计：卡可点击筛选；.task-brief 是 tasks-flow e2e 的断言锚点，保留 -->
    <div class="task-brief app-metric-grid-4" aria-label="任务摘要">
      <button
        type="button"
        class="app-stat task-stat-card"
        :class="{ 'is-active': statsTotalActive }"
        :aria-pressed="statsTotalActive"
        title="显示全部任务"
        @click="applyStatsFilter('total')"
      >
        <span class="app-stat-label">{{ totalCardLabel }}</span>
        <div class="app-stat-value">{{ statsTotal }}<small> 项任务</small></div>
      </button>
      <button
        type="button"
        class="app-stat task-stat-card"
        :class="{ 'is-active': statsInProgressActive }"
        :aria-pressed="statsInProgressActive"
        title="只看进行中（可叠加逾期/共享）"
        @click="applyStatsFilter('in_progress')"
      >
        <span class="app-stat-label">进行中</span>
        <div class="app-stat-value">{{ inProgressCount }}<small> 项进行中</small></div>
      </button>
      <button
        type="button"
        class="app-stat task-stat-card"
        :class="{ 'is-alert': overdueCount, 'is-active': statsOverdueActive }"
        :aria-pressed="statsOverdueActive"
        title="只看已逾期（可叠加进行中/共享）"
        @click="applyStatsFilter('overdue')"
      >
        <span class="app-stat-label">已逾期</span>
        <div class="app-stat-value">{{ overdueCount }}<small> 项逾期</small></div>
      </button>
      <button
        type="button"
        class="app-stat task-stat-card"
        :class="{ 'is-active': statsSharedActive }"
        :aria-pressed="statsSharedActive"
        title="只看共享任务（可叠加进行中/逾期）"
        @click="applyStatsFilter('shared')"
      >
        <span class="app-stat-label">共享任务</span>
        <div class="app-stat-value">{{ sharedTaskCount }}<small> 项共享</small></div>
      </button>
      <button
        type="button"
        class="app-stat task-stat-card"
        :class="{ 'is-active': statsParticipatingActive }"
        :aria-pressed="statsParticipatingActive"
        title="只看我参与的（可叠加进行中/逾期）"
        @click="applyStatsFilter('participating')"
      >
        <span class="app-stat-label">我参与的</span>
        <div class="app-stat-value">{{ participatingCount }}<small> 项参与</small></div>
      </button>
    </div>
    <p class="task-stat-hint">统计卡可组合筛选（如「进行中 + 已逾期」），再次点击取消；点任务总数清空。</p>

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
        <div class="task-toolbar app-filter-bar">
          <el-input
            v-model="searchDraft"
            class="task-search"
            placeholder="搜索标题或描述"
            clearable
            @input="scheduleSearch"
            @clear="applySearchNow"
            @keyup.enter="applySearchNow"
          />
          <span class="task-sort-label">排序</span>
          <el-select v-model="sortOrder" class="task-sort" @change="handleSortChange">
            <el-option v-for="item in sortOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-select v-model="priorityFilter" class="task-priority-filter" clearable placeholder="优先级" @change="handlePriorityChange">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
          <button
            type="button"
            class="task-archived-toggle"
            :class="{ 'is-active': archivedFilter }"
            :aria-pressed="archivedFilter"
            @click="toggleArchived"
          >
            {{ archivedFilter ? '返回未归档' : '查看已归档' }}
          </button>
        </div>
        <div v-if="sourceFilterMeta" class="source-filter-row app-chip-row">
          <span class="app-tag is-pending">来源筛选：{{ sourceFilterMeta.label }} #{{ sourceFilterMeta.sourceId }}</span>
          <el-button size="small" text type="info" @click="clearSourceFilter">清除来源筛选</el-button>
        </div>
        <div v-if="viewMode === 'table' && selectedTaskIds.length" class="task-bulk-bar">
          <span class="task-bulk-count">已选 {{ selectedTaskIds.length }} 项</span>
          <el-select v-if="members.length" v-model="bulkAssigneeId" clearable filterable placeholder="新负责人（留空=清除）" style="width: 220px">
            <el-option
              v-for="member in members"
              :key="member.user_id"
              :label="member.full_name || member.username || `用户 ${member.user_id}`"
              :value="member.user_id"
            />
          </el-select>
          <span v-else class="task-bulk-count">没有可指派的组织成员</span>
          <el-button type="primary" :loading="bulkLoading" :disabled="!members.length" @click="submitBulkReassign">批量改派</el-button>
          <span class="task-bulk-divider" aria-hidden="true"></span>
          <el-select v-model="bulkAction" placeholder="批量操作" style="width: 130px">
            <el-option label="标记完成" value="complete" />
            <el-option label="归档" value="archive" />
            <el-option label="修改优先级" value="priority" />
          </el-select>
          <el-select v-if="bulkAction === 'priority'" v-model="bulkPriority" style="width: 100px">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
          <el-button type="primary" :loading="bulkLoading" :disabled="!bulkAction" @click="submitBulkAction">执行</el-button>
          <el-button text @click="clearTableSelection">取消</el-button>
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
                      {{ taskSourceLabel(task.source_type) }}
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

          <el-table
            v-else
            ref="taskTableRef"
            v-loading="loading"
            :data="tasks"
            border
            :empty-text="taskEmptyText"
            class="task-table"
            @selection-change="handleTableSelectionChange"
          >
            <el-table-column type="selection" width="42" />
            <el-table-column prop="title" label="标题" />
            <el-table-column prop="description" label="事项说明" show-overflow-tooltip />
            <el-table-column prop="case_title" label="案件" width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ row.case_title || '—' }}</template>
            </el-table-column>
            <el-table-column prop="assignee" label="负责人" width="110" show-overflow-tooltip>
              <template #default="{ row }">{{ row.assignee || '—' }}</template>
            </el-table-column>
            <el-table-column prop="due_date" label="截止" width="100">
              <template #default="{ row }">
                <span v-if="row.due_date" class="task-due" :class="{ 'is-overdue': isTaskOverdue(row) }">
                  {{ dueDateText(row.due_date) }}
                </span>
                <span v-else>—</span>
              </template>
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
                  {{ taskSourceLabel(row.source_type) }}
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
      :can-collaborate="canCollaborateSelectedTask"
      @refresh="fetchTasks"
      @task-updated="handleTaskUpdated"
      @status-change="changeStatus"
      @decompose="decompose"
      @open-source="openSource"
      @open-case="openCase"
      @open-agent-run="openAgentRun"
      @archive="handleArchive"
      @unarchive="handleUnarchive"
    />

    <TaskCreateDialogs
      ref="createDialogs"
      :loading="loading"
      :case-id="workflowCaseId"
      :organization-id="currentUser?.organization_id || null"
      @refresh="handleDialogsRefresh"
    />
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../api'
import legalWorkspace from '../api/legalWorkspace'
import { ElMessage } from 'element-plus/es/components/message/index'
import QueryStateView from '../components/QueryStateView.vue'
import { normalizeError } from '../api/errors.js'
import { getStatusLabel } from '../utils/status'
import { taskSourceLabel } from '../utils/taskSource'
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
const taskStats = ref(null)
const taskTableRef = ref(null)
const selectedTaskIds = ref([])
const members = ref([])
const bulkAssigneeId = ref(null)
const bulkAction = ref('')
const bulkPriority = ref('medium')
const bulkLoading = ref(false)
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
const statusFilter = ref(
  typeof route.query.status === 'string' && ['todo', 'in_progress', 'done', 'cancelled'].includes(route.query.status)
    ? route.query.status
    : ''
)
const overdueFilter = ref(route.query.overdue === '1' || route.query.overdue === 'true')
const priorityFilter = ref(
  typeof route.query.priority === 'string' && ['low', 'medium', 'high'].includes(route.query.priority)
    ? route.query.priority
    : ''
)
const archivedFilter = ref(route.query.archived === '1' || route.query.archived === 'true')
const detailVisible = ref(false)
const selectedTask = ref(null)
const workflowCaseId = computed(() => {
  const value = Number(route.query.case_id)
  return Number.isFinite(value) && value > 0 ? value : null
})
// 案件任务视图标题：优先案件名，取不到时回退 #id，不阻塞任务列表。
const caseTitle = ref('')
const caseLabel = computed(() => caseTitle.value || (workflowCaseId.value ? `#${workflowCaseId.value}` : ''))

const loadCaseTitle = async (caseId) => {
  if (!caseId) {
    caseTitle.value = ''
    return
  }
  try {
    const { data } = await legalWorkspace.getMatterSummary(caseId)
    if (workflowCaseId.value !== caseId) return
    caseTitle.value = data?.case?.title || data?.matter?.title || ''
  } catch {
    if (workflowCaseId.value === caseId) caseTitle.value = ''
  }
}

const sortOptions = [
  { label: '最近创建', value: 'created_desc' },
  { label: '截止最近', value: 'due_asc' },
  { label: '截止最远', value: 'due_desc' },
]
const normalizeSort = (value) => (sortOptions.some((item) => item.value === value) ? value : 'created_desc')
const searchDraft = ref(typeof route.query.q === 'string' ? route.query.q : '')
const sortOrder = ref(normalizeSort(route.query.sort))
let searchTimer = null

const applySearchQuery = (value) => {
  const keyword = String(value || '').trim()
  const current = typeof route.query.q === 'string' ? route.query.q : ''
  if (keyword === current) return
  const nextQuery = { ...route.query }
  if (keyword) nextQuery.q = keyword
  else delete nextQuery.q
  taskPage.value = 1
  router.replace({ query: nextQuery })
  fetchTasks()
}

const scheduleSearch = () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => applySearchQuery(searchDraft.value), 350)
}

const applySearchNow = () => {
  clearTimeout(searchTimer)
  applySearchQuery(searchDraft.value)
}

const handleSortChange = () => {
  const nextQuery = { ...route.query }
  if (sortOrder.value === 'created_desc') delete nextQuery.sort
  else nextQuery.sort = sortOrder.value
  taskPage.value = 1
  router.replace({ query: nextQuery })
  fetchTasks()
}

const applyStatsFilter = (kind) => {
  const nextQuery = { ...route.query }
  if (kind === 'total') {
    if (statsTotalActive.value) return
    delete nextQuery.status
    delete nextQuery.overdue
    delete nextQuery.scope
    delete nextQuery.priority
  } else if (kind === 'in_progress') {
    if (nextQuery.status === 'in_progress') delete nextQuery.status
    else nextQuery.status = 'in_progress'
  } else if (kind === 'overdue') {
    if (nextQuery.overdue === '1' || nextQuery.overdue === 'true') delete nextQuery.overdue
    else nextQuery.overdue = '1'
  } else if (kind === 'shared') {
    if (nextQuery.scope === 'shared') delete nextQuery.scope
    else nextQuery.scope = 'shared'
  } else if (kind === 'participating') {
    if (nextQuery.scope === 'participating') delete nextQuery.scope
    else nextQuery.scope = 'participating'
  } else {
    return
  }
  statusFilter.value = typeof nextQuery.status === 'string'
    && ['todo', 'in_progress', 'done', 'cancelled'].includes(nextQuery.status)
    ? nextQuery.status
    : ''
  overdueFilter.value = nextQuery.overdue === '1' || nextQuery.overdue === 'true'
  priorityFilter.value = typeof nextQuery.priority === 'string'
    && ['low', 'medium', 'high'].includes(nextQuery.priority)
    ? nextQuery.priority
    : ''
  scopeFilter.value = typeof nextQuery.scope === 'string' && scopeOptions.some((item) => item.value === nextQuery.scope)
    ? nextQuery.scope
    : 'all'
  taskPage.value = 1
  router.replace({ query: nextQuery })
  fetchTasks()
}

onBeforeUnmount(() => clearTimeout(searchTimer))

const priorityLabelMap = {
  high: '高',
  medium: '中',
  low: '低',
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
  { label: '我参与的', value: 'participating' },
  { label: '部门共享', value: 'department' },
  { label: '组织共享', value: 'organization' },
  { label: '共享任务', value: 'shared' },
]

const canEditSelectedTask = computed(() => {
  if (!selectedTask.value || !currentUser.value) return false
  return selectedTask.value.user_id === currentUser.value.id
})

const canCollaborateSelectedTask = computed(() => {
  if (!selectedTask.value || !currentUser.value) return false
  return canEditSelectedTask.value
    || selectedTask.value.assignee_user_id === currentUser.value.id
    || (selectedTask.value.collaborator_user_ids || []).includes(currentUser.value.id)
})

const sourceFilterMeta = computed(() => {
  const sourceType = typeof route.query.sourceType === 'string' ? route.query.sourceType : ''
  const sourceId = Number(route.query.sourceId)
  if (!sourceType || !Number.isFinite(sourceId) || sourceId <= 0) return null
  return {
    sourceType,
    sourceId,
    label: taskSourceLabel(sourceType),
  }
})

const taskEmptyText = computed(() => {
  const keyword = searchDraft.value.trim()
  if (keyword) return `没有匹配「${keyword}」的任务`
  const labels = []
  if (statusFilter.value === 'in_progress') labels.push('进行中')
  if (priorityFilter.value) labels.push(`优先级${priorityFilter.value === 'high' ? '高' : priorityFilter.value === 'medium' ? '中' : '低'}`)
  if (overdueFilter.value) labels.push('逾期')
  if (scopeFilter.value === 'shared') labels.push('共享')
  if (scopeFilter.value === 'participating') labels.push('我参与的')
  if (labels.length) return `当前没有${labels.join('且')}的${archivedFilter.value ? '已归档' : ''}任务`
  if (archivedFilter.value) return '暂无已归档任务'
  if (workflowCaseId.value) {
    return caseTitle.value ? `案件「${caseTitle.value}」暂无任务` : `案件 #${workflowCaseId.value} 暂无任务`
  }
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

const inProgressCount = computed(() => (
  taskStats.value?.in_progress ?? tasks.value.filter((item) => item.status === 'in_progress').length
))
const overdueCount = computed(() => (
  taskStats.value?.overdue ?? tasks.value.filter((item) => isTaskOverdue(item)).length
))
const sharedTaskCount = computed(() => (
  taskStats.value?.shared ?? tasks.value.filter((item) => !['我的', '我负责', '我参与'].includes(scopeLabel(item))).length
))
const participatingCount = computed(() => {
  if (taskStats.value?.participating != null) return taskStats.value.participating
  const me = currentUser.value?.id
  return tasks.value.filter((item) => (
    item.user_id === me
    || item.assignee_user_id === me
    || (item.collaborator_user_ids || []).includes(me)
  )).length
})
const statsTotal = computed(() => (
  taskStats.value?.total ?? (taskTotal.value || tasks.value.length)
))
const statsTotalActive = computed(() => (
  !statusFilter.value && !overdueFilter.value && !priorityFilter.value && scopeFilter.value === 'all'
))
const statsInProgressActive = computed(() => statusFilter.value === 'in_progress')
const statsOverdueActive = computed(() => overdueFilter.value)
const statsSharedActive = computed(() => scopeFilter.value === 'shared')
const statsParticipatingActive = computed(() => scopeFilter.value === 'participating')
const statFiltersActive = computed(() => (
  Boolean(statusFilter.value) || overdueFilter.value || priorityFilter.value || scopeFilter.value !== 'all'
))
const totalCardLabel = computed(() => {
  if (statFiltersActive.value) return '当前结果'
  return archivedFilter.value ? '归档任务' : '任务总数'
})

const getTasksByStatus = (status) => tasks.value.filter(t => t.status === status)

const scopeLabel = (item) => {
  if (!item || !currentUser.value) return '我的'
  if (item.user_id === currentUser.value.id) return '我的'
  if (item.assignee_user_id === currentUser.value.id) return '我负责'
  if ((item.collaborator_user_ids || []).includes(currentUser.value.id)) return '我参与'
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
const dueDateText = (value) => String(value || '').slice(0, 10)

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
  if (!task?.source_id) return
  if (task.source_type === 'document') {
    const query = { documentId: String(task.source_id) }
    if (task.case_id) query.case_id = String(task.case_id)
    router.push({ path: '/documents', query })
    return
  }
  if (task.source_type === 'legal_consultation' && task.case_id) {
    router.push({
      path: '/legal-workspace',
      query: {
        case_id: String(task.case_id),
        tab: 'consultation',
        consultation_id: String(task.source_id),
      },
    })
  }
}

const openAgentRun = (runId) => {
  router.push({ path: '/agent', query: { runId: String(runId) } })
}

const openCase = (task) => {
  if (!task?.case_id) return
  router.push({ path: '/legal-workspace', query: { case_id: String(task.case_id) } })
}

const handleTaskUpdated = (task) => {
  if (task && selectedTask.value?.id === task.id) {
    selectedTask.value = task
  }
}

let fetchSeq = 0

const fetchTasks = async () => {
  // 搜索/排序会连续触发请求：只让最后一次落盘，避免旧响应覆盖新列表
  const seq = ++fetchSeq
  loading.value = true
  try {
    const { data } = await api.listTasks(null, {
      page: taskPage.value,
      page_size: taskPageSize.value,
      scope: scopeFilter.value,
      status: statusFilter.value || undefined,
      priority: priorityFilter.value || undefined,
      overdue: overdueFilter.value || undefined,
      archived: archivedFilter.value || undefined,
      source_type: sourceFilterMeta.value?.sourceType || undefined,
      source_id: sourceFilterMeta.value?.sourceId || undefined,
      case_id: workflowCaseId.value || undefined,
      q: searchDraft.value.trim() || undefined,
      sort: sortOrder.value,
    })
    if (seq !== fetchSeq) return
    tasks.value = data?.items || []
    taskTotal.value = data?.total || 0
    taskStats.value = data?.stats || null
  } catch (e) {
    if (seq === fetchSeq) taskError.value = normalizeError(e)
  } finally {
    if (seq === fetchSeq) loading.value = false
  }
}

const handleScopeChange = async () => {
  const nextQuery = { ...route.query, view: viewMode.value }
  if (scopeFilter.value === 'all') delete nextQuery.scope
  else nextQuery.scope = scopeFilter.value
  taskPage.value = 1
  await router.replace({ query: nextQuery })
  await fetchTasks()
}

const handlePriorityChange = async () => {
  const nextQuery = { ...route.query }
  if (priorityFilter.value) nextQuery.priority = priorityFilter.value
  else delete nextQuery.priority
  taskPage.value = 1
  await router.replace({ query: nextQuery })
  await fetchTasks()
}

const loadTaskMembers = async () => {
  const orgId = currentUser.value?.organization_id
  if (!orgId) {
    members.value = []
    return
  }
  try {
    const { data } = await api.listOrgMembers(orgId)
    members.value = data || []
  } catch {
    members.value = []
  }
}

const handleTableSelectionChange = (rows) => {
  selectedTaskIds.value = rows.map((row) => row.id)
}

const clearTableSelection = () => {
  selectedTaskIds.value = []
  bulkAssigneeId.value = null
  bulkAction.value = ''
  bulkPriority.value = 'medium'
  taskTableRef.value?.clearSelection()
}

const submitBulkReassign = async () => {
  if (!selectedTaskIds.value.length) return
  bulkLoading.value = true
  try {
    const { data } = await api.bulkReassignTasks({
      task_ids: selectedTaskIds.value,
      assignee_user_id: bulkAssigneeId.value ?? null,
    })
    const skipped = data?.skipped || 0
    ElMessage.success(`已改派 ${data?.updated || 0} 项${skipped ? `，跳过 ${skipped} 项` : ''}`)
    clearTableSelection()
    await fetchTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '批量改派失败')
  } finally {
    bulkLoading.value = false
  }
}

const submitBulkAction = async () => {
  if (!selectedTaskIds.value.length || !bulkAction.value) return
  bulkLoading.value = true
  try {
    const { data } = await api.bulkActionTasks({
      task_ids: selectedTaskIds.value,
      action: bulkAction.value,
      priority: bulkAction.value === 'priority' ? bulkPriority.value : undefined,
    })
    const skipped = data?.skipped || 0
    const labels = { complete: '完成', archive: '归档', priority: '修改优先级' }
    ElMessage.success(`已${labels[bulkAction.value] || '处理'} ${data?.updated || 0} 项${skipped ? `，跳过 ${skipped} 项` : ''}`)
    clearTableSelection()
    await fetchTasks()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '批量操作失败')
  } finally {
    bulkLoading.value = false
  }
}

const toggleArchived = () => {
  const nextQuery = { ...route.query }
  if (archivedFilter.value) delete nextQuery.archived
  else nextQuery.archived = '1'
  archivedFilter.value = !archivedFilter.value
  taskPage.value = 1
  router.replace({ query: nextQuery })
  fetchTasks()
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

const handleArchive = async (task) => {
  try {
    await api.archiveTask(task.id)
    ElMessage.success('任务已归档')
    detailVisible.value = false
    await fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '归档失败') }
}

const handleUnarchive = async (task) => {
  try {
    await api.unarchiveTask(task.id)
    ElMessage.success('任务已恢复')
    detailVisible.value = false
    await fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '恢复失败') }
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
  loadTaskMembers()
  if (route.query.view === 'table' || route.query.taskId) {
    viewMode.value = 'table'
  }
  if (typeof route.query.scope === 'string' && scopeOptions.some(item => item.value === route.query.scope)) {
    scopeFilter.value = route.query.scope
  }
  if (workflowCaseId.value) loadCaseTitle(workflowCaseId.value)
  await fetchTasks()
  await applyTaskRoute(route.query.taskId)
})

watch(viewMode, (value) => {
  const nextQuery = { ...route.query, view: value, scope: scopeFilter.value }
  router.replace({ query: nextQuery })
})

watch(
  () => route.query.status,
  async (value, oldValue) => {
    if (value === oldValue) return
    const status = typeof value === 'string' && ['todo', 'in_progress', 'done', 'cancelled'].includes(value)
      ? value
      : ''
    if (status === statusFilter.value) return
    statusFilter.value = status
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => route.query.overdue,
  async (value, oldValue) => {
    if (value === oldValue) return
    const overdue = value === '1' || value === 'true'
    if (overdue === overdueFilter.value) return
    overdueFilter.value = overdue
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => route.query.priority,
  async (value, oldValue) => {
    if (value === oldValue) return
    const priority = typeof value === 'string' && ['low', 'medium', 'high'].includes(value)
      ? value
      : ''
    if (priority === priorityFilter.value) return
    priorityFilter.value = priority
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => route.query.archived,
  async (value, oldValue) => {
    if (value === oldValue) return
    const archived = value === '1' || value === 'true'
    if (archived === archivedFilter.value) return
    archivedFilter.value = archived
    taskPage.value = 1
    await fetchTasks()
  }
)

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
    loadCaseTitle(workflowCaseId.value)
    await fetchTasks()
  }
)

watch(
  () => route.query.scope,
  async (value, oldValue) => {
    if (value === oldValue) return
    const next = typeof value === 'string' && scopeOptions.some((item) => item.value === value)
      ? value
      : 'all'
    if (next === scopeFilter.value) return
    scopeFilter.value = next
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

watch(
  () => route.query.q,
  async (value, oldValue) => {
    if (value === oldValue) return
    const keyword = typeof value === 'string' ? value : ''
    if (keyword === searchDraft.value) return
    searchDraft.value = keyword
    taskPage.value = 1
    await fetchTasks()
  }
)

watch(
  () => route.query.sort,
  async (value, oldValue) => {
    if (value === oldValue) return
    const next = normalizeSort(value)
    if (next === sortOrder.value) return
    sortOrder.value = next
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
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
@media (max-width: 1100px) {
  .task-brief {
    grid-template-columns: 1fr;
  }
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
.task-toolbar {
  margin-bottom: var(--space-3);
}
.task-search {
  width: 280px;
}
.task-sort-label {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.task-sort {
  width: 132px;
}
.task-priority-filter {
  width: 104px;
}
.task-bulk-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}
.task-bulk-count {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.task-bulk-divider {
  width: 1px;
  height: 22px;
  background: var(--color-border);
}
.task-archived-toggle {
  margin-left: auto;
  padding: 6px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}
.task-archived-toggle:hover,
.task-archived-toggle.is-active {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.task-stat-card {
  width: 100%;
  appearance: none;
  border: 1px solid transparent;
  text-align: left;
  font: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.task-stat-card:hover {
  border-color: var(--color-primary);
}
.task-stat-card.is-active {
  border-color: var(--color-primary);
  box-shadow: inset 0 0 0 1px var(--color-primary);
}
.task-stat-card:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}
.task-stat-hint {
  margin: calc(var(--space-2) * -1) 0 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
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
.task-due.is-overdue {
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
