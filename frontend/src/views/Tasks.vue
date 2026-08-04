<template>
  <div class="task-page">
    <div class="page-heading">
      <div>
        <h3>待办任务</h3>
        <p>统一查看来源任务、协作进度、同步邮件和执行记录。</p>
      </div>
    </div>

    <div class="task-overview">
      <div class="overview-tile">
        <span>任务总数</span>
        <strong>{{ taskTotal || tasks.length }}</strong>
        <p>当前筛选范围内可见任务</p>
      </div>
      <div class="overview-tile">
        <span>进行中</span>
        <strong>{{ inProgressCount }}</strong>
        <p>正在推进的执行项</p>
      </div>
      <div class="overview-tile">
        <span>已逾期</span>
        <strong>{{ overdueCount }}</strong>
        <p>需要同步或催办的任务</p>
      </div>
      <div class="overview-tile">
        <span>共享任务</span>
        <strong>{{ sharedTaskCount }}</strong>
        <p>部门或组织共享可见任务</p>
      </div>
    </div>

    <el-card class="toolbar-card">
      <template #header>
        <div class="section-header">
          <div>
            <div class="section-eyebrow">Task Center</div>
            <span>任务操作台</span>
          </div>
        </div>
      </template>
      <el-space wrap class="toolbar-actions">
        <el-button type="primary" @click="showCreateDialog = true">新建任务</el-button>
        <el-button type="success" :loading="syncingEmail" @click="generateTaskSyncEmail(false)">生成同步邮件</el-button>
        <el-button type="info" plain :loading="syncingEmail" @click="generateTaskSyncEmail(true)">仅逾期任务邮件</el-button>
        <el-button type="warning" @click="showExtractDialog = true">从文档提取</el-button>
        <el-button type="info" @click="showChatExtractDialog = true">从聊天提取</el-button>
        <el-select v-model="syncEmailScope" style="width: 140px">
          <el-option v-for="item in syncScopeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-select v-model="scopeFilter" style="width: 140px" @change="handleScopeChange">
          <el-option v-for="item in scopeOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-radio-group v-model="viewMode" style="margin-left: 16px">
          <el-radio-button value="kanban">看板</el-radio-button>
          <el-radio-button value="table">表格</el-radio-button>
        </el-radio-group>
      </el-space>
      <div v-if="sourceFilterMeta" class="source-filter-row">
        <el-tag size="small" type="warning">来源筛选：{{ sourceFilterMeta.label }} #{{ sourceFilterMeta.sourceId }}</el-tag>
        <el-button size="small" text type="info" @click="clearSourceFilter">清除来源筛选</el-button>
      </div>
    </el-card>

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
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="description" label="描述" show-overflow-tooltip />
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
        <el-table-column prop="parent_id" label="父任务" width="80">
          <template #default="{ row }">{{ row.parent_id || '-' }}</template>
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

    <el-dialog v-model="detailVisible" title="任务详情" width="720px" class="task-dialog">
      <template v-if="selectedTask">
        <div class="detail-metrics">
          <div class="detail-metric">
            <span>当前状态</span>
            <strong>{{ selectedTask.title }}</strong>
            <StatusTag kind="task" :status="selectedTask.status" size="small" />
          </div>
          <div class="detail-metric">
            <span>执行归属</span>
            <strong>{{ selectedTask.assignee || '待分配' }}</strong>
            <el-tag size="small" :type="scopeTagType(selectedTask)">{{ scopeLabel(selectedTask) }}</el-tag>
          </div>
          <div class="detail-metric">
            <span>完成进度</span>
            <strong>{{ selectedTask.progress || 0 }}%</strong>
            <span class="inline-hint">优先级 {{ priorityLabelMap[selectedTask.priority] || selectedTask.priority }}</span>
          </div>
        </div>

        <el-descriptions :column="2" border class="detail-descriptions">
          <el-descriptions-item label="ID">{{ selectedTask.id }}</el-descriptions-item>
          <el-descriptions-item label="标题">{{ selectedTask.title }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <StatusTag kind="task" :status="selectedTask.status" />
          </el-descriptions-item>
          <el-descriptions-item label="优先级">
            <el-tag :type="selectedTask.priority === 'high' ? 'danger' : selectedTask.priority === 'medium' ? 'warning' : 'info'">
              {{ priorityLabelMap[selectedTask.priority] || selectedTask.priority }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="负责人">{{ selectedTask.assignee || '-' }}</el-descriptions-item>
          <el-descriptions-item label="协作者">{{ (selectedTask.collaborators || []).join('、') || '-' }}</el-descriptions-item>
          <el-descriptions-item label="进度">{{ selectedTask.progress || 0 }}%</el-descriptions-item>
          <el-descriptions-item label="来源">
            <el-tag v-if="selectedTask.source_type" size="small" type="info">
              {{ sourceTypeLabelMap[selectedTask.source_type] || selectedTask.source_type }}
              <span v-if="selectedTask.source_id"> #{{ selectedTask.source_id }}</span>
            </el-tag>
            <span v-else>手动创建</span>
          </el-descriptions-item>
          <el-descriptions-item label="父任务">{{ selectedTask.parent_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ selectedTask.description || '-' }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="subTasks.length" class="detail-section">
          <h4>子任务 ({{ subTasks.length }})</h4>
          <el-table :data="subTasks" border size="small" class="detail-table">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="title" label="标题" />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <StatusTag kind="task" :status="row.status" size="small" />
              </template>
            </el-table-column>
          </el-table>
        </div>

        <div class="detail-action-row">
          <el-button type="warning" :disabled="!canEditSelectedTask" @click="decompose(selectedTask); detailVisible = false">拆解任务</el-button>
          <el-button
            v-if="selectedTask.source_type === 'document' && selectedTask.source_id"
            type="info"
            @click="openSource(selectedTask)"
          >
            查看来源文档
          </el-button>
          <el-button v-if="selectedTask.status !== 'in_progress'" type="primary" :disabled="!canEditSelectedTask" @click="changeStatus(selectedTask, 'in_progress')">开始执行</el-button>
          <el-button v-if="selectedTask.status !== 'done'" type="success" :disabled="!canEditSelectedTask" @click="changeStatus(selectedTask, 'done')">标记完成</el-button>
        </div>
        <div v-if="!canEditSelectedTask" class="readonly-note app-readonly-banner">
          共享任务当前仅支持查看，更新、评论和状态变更需由创建人操作
        </div>

        <div class="detail-section">
          <h4>协作更新</h4>
          <div class="editor-row">
            <el-input v-model.number="progressDraft" :disabled="!canEditSelectedTask" type="number" style="width: 120px" placeholder="进度 %" />
            <el-input v-model="collaboratorsDraft" :disabled="!canEditSelectedTask" placeholder="协作者，逗号分隔" style="width: 260px" />
            <el-button type="primary" :disabled="!canEditSelectedTask" @click="updateCollaboration">保存协作信息</el-button>
          </div>
        </div>

        <div class="detail-section">
          <h4>评论</h4>
          <div class="editor-row editor-row-top">
            <el-input v-model="commentDraft" :disabled="!canEditSelectedTask" type="textarea" :rows="2" placeholder="记录进展、阻塞项或协作说明" style="flex: 1" />
            <el-button type="primary" :disabled="!canEditSelectedTask" @click="submitComment">添加评论</el-button>
          </div>
          <div v-if="comments.length" class="comment-list">
            <div v-for="item in comments" :key="`comment-${item.id}`" class="comment-item">
              <div class="comment-meta">用户 {{ item.user_id }} · {{ item.created_at }}</div>
              <div class="comment-content">{{ item.content }}</div>
            </div>
          </div>
        </div>

        <div v-if="taskLogs.length" class="detail-section">
          <h4>操作日志</h4>
          <el-table :data="taskLogs" border size="small" class="detail-table">
            <el-table-column prop="action" label="动作" width="160" />
            <el-table-column prop="detail" label="详情" show-overflow-tooltip />
            <el-table-column prop="created_at" label="时间" width="180" />
          </el-table>
        </div>

        <div class="detail-section">
          <h4>关联 Agent 执行</h4>
          <div v-if="relatedAgentRuns.length" class="agent-run-list">
            <div v-for="item in relatedAgentRuns" :key="`task-agent-${item.id}`" class="agent-run-item">
              <div class="agent-run-top">
                <strong>#{{ item.id }} {{ item.goal }}</strong>
                <StatusTag kind="agent" :status="item.status" size="small" />
              </div>
              <div class="agent-run-meta">{{ item.created_at }} · 步数 {{ item.total_steps || 0 }}</div>
              <el-button size="small" text type="primary" @click="openAgentRun(item.id)">查看执行</el-button>
            </div>
          </div>
          <div v-else class="readonly-note app-state-banner">暂无关联 Agent 执行记录</div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showCreateDialog" title="新建任务" width="560px" class="task-dialog">
      <div class="form-grid">
        <el-input v-model="newTask.title" placeholder="任务标题" />
        <el-input v-model="newTask.description" type="textarea" :rows="3" placeholder="任务描述（可选）" />
        <el-input v-model="newTask.assignee" placeholder="负责人（可选）" />
        <el-input v-model="newTask.collaborators" placeholder="协作者，逗号分隔" />
        <div class="form-inline">
          <el-input v-model.number="newTask.progress" type="number" placeholder="进度 0-100" />
          <el-select v-model="newTask.priority">
            <el-option label="高优先级" value="high" />
            <el-option label="中优先级" value="medium" />
            <el-option label="低优先级" value="low" />
          </el-select>
        </div>
      </div>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="createTask">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showExtractDialog" title="从文档提取任务" width="500px" class="task-dialog">
      <div class="form-grid">
        <div class="dialog-copy">输入来源文档 ID，系统会抽取待办并自动落成任务。</div>
        <el-input v-model.number="extractDocId" placeholder="输入文档 ID" type="number" />
      </div>
      <template #footer>
        <el-button @click="showExtractDialog = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="extractFromDoc">提取</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showChatExtractDialog" title="从聊天提取任务" width="500px" class="task-dialog">
      <div class="form-grid">
        <div class="dialog-copy">输入聊天内容，系统会识别执行项、责任人和时间要求。</div>
        <el-input v-model="chatMessage" type="textarea" :rows="3" placeholder="输入聊天消息，例如：明天下午 3 点开会讨论方案" />
      </div>
      <template #footer>
        <el-button @click="showChatExtractDialog = false">取消</el-button>
        <el-button type="primary" :loading="loading" @click="extractFromChat">提取</el-button>
      </template>
    </el-dialog>
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

const route = useRoute()
const router = useRouter()
const currentUser = ref(null)
const tasks = ref([])
const taskPage = ref(1)
const taskPageSize = ref(12)
const taskTotal = ref(0)
const loading = ref(false)
const syncingEmail = ref(false)
const viewMode = ref('kanban')
const scopeFilter = ref('all')
const syncEmailScope = ref('mine')
const detailVisible = ref(false)
const selectedTask = ref(null)
const subTasks = ref([])
const comments = ref([])
const taskLogs = ref([])
const relatedAgentRuns = ref([])
const commentDraft = ref('')
const progressDraft = ref(0)
const collaboratorsDraft = ref('')

const showCreateDialog = ref(false)
const newTask = ref({ title: '', description: '', assignee: '', collaborators: '', priority: 'medium', progress: 0 })

const showExtractDialog = ref(false)
const extractDocId = ref(null)

const showChatExtractDialog = ref(false)
const chatMessage = ref('')

const priorityLabelMap = {
  high: '高',
  medium: '中',
  low: '低',
}

const sourceTypeLabelMap = {
  meeting: '会议',
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

const syncScopeOptions = [
  { label: '我的任务邮件', value: 'mine' },
  { label: '部门共享任务', value: 'department' },
  { label: '组织共享任务', value: 'organization' },
  { label: '全部可见任务', value: 'all' },
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

const createTask = async () => {
  if (!newTask.value.title.trim()) return ElMessage.warning('请输入标题')
  loading.value = true
  try {
    await api.createTask({
      ...newTask.value,
      collaborators: newTask.value.collaborators
        ? newTask.value.collaborators.split(',').map((item) => item.trim()).filter(Boolean)
        : [],
    })
    ElMessage.success('创建成功')
    showCreateDialog.value = false
    newTask.value = { title: '', description: '', assignee: '', collaborators: '', priority: 'medium', progress: 0 }
    fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '创建失败') }
  loading.value = false
}

const changeStatus = async (task, status) => {
  try {
    await api.updateTask(task.id, { status })
    ElMessage.success('状态已更新')
    fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '更新失败') }
}

const generateTaskSyncEmail = async (overdueOnly) => {
  syncingEmail.value = true
  try {
    const { data } = await api.generateTaskSyncEmail({
      include_overdue_only: overdueOnly,
      purpose: overdueOnly ? '逾期任务催办' : '任务进度同步',
      tone: 'professional',
      need_action: true,
      scope: syncEmailScope.value,
    })
    ElMessage.success(`已生成邮件草稿 #${data.draft.id}（邮件查看页面已下线，草稿保留在后台）`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '同步邮件生成失败')
  }
  syncingEmail.value = false
}

const decompose = async (task) => {
  loading.value = true
  try {
    const { data } = await api.decomposeTask(task.id)
    ElMessage.success(`已拆解为 ${data.created_sub_tasks} 个子任务`)
    fetchTasks()
    if (selectedTask.value && selectedTask.value.id === task.id) {
      loadSubTasks(task.id)
    }
  } catch (e) { ElMessage.error(e.response?.data?.detail || '拆解失败') }
  loading.value = false
}

const selectTask = async (task) => {
  selectedTask.value = task
  progressDraft.value = task.progress || 0
  collaboratorsDraft.value = (task.collaborators || []).join(', ')
  commentDraft.value = ''
  detailVisible.value = true
  replaceTaskQuery(task.id)
  await Promise.all([loadSubTasks(task.id), loadComments(task.id), loadTaskLogs(task.id), loadRelatedAgentRuns(task.id)])
}

const loadSubTasks = async (taskId) => {
  try {
    const { data } = await api.getSubTasks(taskId)
    subTasks.value = data?.items || []
  } catch {
    subTasks.value = []
  }
}

const loadComments = async (taskId) => {
  try {
    const { data } = await api.listTaskComments(taskId)
    comments.value = data || []
  } catch {
    comments.value = []
  }
}

const loadTaskLogs = async (taskId) => {
  try {
    const { data } = await api.listTaskLogs(taskId)
    taskLogs.value = data || []
  } catch {
    taskLogs.value = []
  }
}

const loadRelatedAgentRuns = async (taskId) => {
  try {
    const { data } = await api.listAgentRuns({ artifact_type: 'task', artifact_id: taskId, page: 1, page_size: 5 })
    relatedAgentRuns.value = data?.items || []
  } catch {
    relatedAgentRuns.value = []
  }
}

const updateCollaboration = async () => {
  if (!selectedTask.value) return
  try {
    await api.updateTask(selectedTask.value.id, {
      progress: progressDraft.value,
      collaborators: collaboratorsDraft.value
        ? collaboratorsDraft.value.split(',').map((item) => item.trim()).filter(Boolean)
        : [],
    })
    ElMessage.success('协作信息已更新')
    await fetchTasks()
    const { data } = await api.getTask(selectedTask.value.id)
    selectedTask.value = data
    await loadTaskLogs(selectedTask.value.id)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '协作信息更新失败')
  }
}

const submitComment = async () => {
  if (!selectedTask.value || !commentDraft.value.trim()) return
  try {
    await api.addTaskComment(selectedTask.value.id, { content: commentDraft.value.trim() })
    commentDraft.value = ''
    ElMessage.success('评论已添加')
    await Promise.all([loadComments(selectedTask.value.id), loadTaskLogs(selectedTask.value.id)])
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '添加评论失败')
  }
}

const extractFromDoc = async () => {
  if (!extractDocId.value) return ElMessage.warning('请输入文档 ID')
  loading.value = true
  try {
    const { data } = await api.extractTasksFromDoc(extractDocId.value)
    ElMessage.success(`从文档提取了 ${data.created_tasks} 个任务`)
    showExtractDialog.value = false
    taskPage.value = 1
    fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '提取失败') }
  loading.value = false
}

const extractFromChat = async () => {
  if (!chatMessage.value.trim()) return ElMessage.warning('请输入消息')
  loading.value = true
  try {
    const { data } = await api.extractTasksFromChat(chatMessage.value)
    ElMessage.success(`从聊天提取了 ${data.created_tasks} 个任务`)
    showChatExtractDialog.value = false
    chatMessage.value = ''
    taskPage.value = 1
    fetchTasks()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '提取失败') }
  loading.value = false
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
  try {
    const { data } = await api.getMe()
    currentUser.value = data
  } catch {
    currentUser.value = null
  }
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
    subTasks.value = []
    comments.value = []
    taskLogs.value = []
    relatedAgentRuns.value = []
    commentDraft.value = ''
    replaceTaskQuery(null)
  }
})
</script>

<style scoped>
.task-page {
  display: grid;
  gap: var(--space-6);
}

.page-heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  align-items: flex-end;
  padding: var(--space-6);
}
.page-heading h3 {
  margin: 0;
  font-size: var(--text-3xl);
  line-height: 1.15;
  color: var(--color-text);
  letter-spacing: 0;
  font-weight: 800;
}
.page-heading p {
  margin: var(--space-2) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--text-base);
  line-height: 1.6;
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.task-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.overview-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.overview-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
  transform: translateY(-2px);
}
.overview-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.overview-tile strong {
  color: var(--color-text);
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  font-weight: 800;
}
.overview-tile p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

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
  transform: translateY(-2px);
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
  .task-overview {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 760px) {
  .task-overview {
    grid-template-columns: 1fr;
  }
  .detail-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
