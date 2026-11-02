<template>
  <el-dialog :model-value="visible" title="任务详情" width="720px" class="task-dialog" @update:model-value="emit('update:visible', $event)">
    <template v-if="task">
      <div class="detail-metrics">
        <div class="detail-metric">
          <span>当前状态</span>
          <strong>{{ task.title }}</strong>
          <StatusTag kind="task" :status="task.status" size="small" />
          <el-tag v-if="task.archived_at" type="info" size="small">已归档</el-tag>
        </div>
        <div class="detail-metric">
          <span>执行归属</span>
          <strong>{{ task.assignee || '待分配' }}</strong>
          <el-tag size="small" :type="scopeTagType(task)">{{ scopeLabel(task) }}</el-tag>
        </div>
        <div class="detail-metric">
          <span>完成进度</span>
          <strong>{{ task.progress || 0 }}%</strong>
          <span class="inline-hint">优先级 {{ priorityLabelMap[task.priority] || task.priority }}</span>
        </div>
      </div>

      <el-descriptions :column="2" border class="detail-descriptions">
        <el-descriptions-item label="ID">{{ task.id }}</el-descriptions-item>
        <el-descriptions-item label="标题">{{ task.title }}</el-descriptions-item>
        <el-descriptions-item label="所属案件" :span="2">
          <span v-if="task.case_id" class="case-entry">
            <span class="case-entry-name">{{ task.case_title || `案件 #${task.case_id}` }}</span>
            <button type="button" class="case-entry-link" @click="emit('open-case', task)">
              打开案件<span aria-hidden="true"> →</span>
            </button>
          </span>
          <span v-else class="inline-hint">未关联案件</span>
        </el-descriptions-item>
        <el-descriptions-item label="状态">
          <StatusTag kind="task" :status="task.status" />
        </el-descriptions-item>
        <el-descriptions-item label="优先级">
          <el-tag :type="task.priority === 'high' ? 'danger' : task.priority === 'medium' ? 'warning' : 'info'">
            {{ priorityLabelMap[task.priority] || task.priority }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="负责人">{{ task.assignee || '-' }}</el-descriptions-item>
        <el-descriptions-item label="协作者">{{ (task.collaborators || []).join('、') || '-' }}</el-descriptions-item>
        <el-descriptions-item label="进度">{{ task.progress || 0 }}%</el-descriptions-item>
        <el-descriptions-item label="截止日期">
          <span :class="{ 'is-overdue-text': isOverdue }">{{ formatDueDate(task.due_date) || '未设置' }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="来源">
          <el-tag v-if="task.source_type" size="small" type="info">
            {{ taskSourceLabel(task.source_type) }}
            <span v-if="task.source_id"> #{{ task.source_id }}</span>
          </el-tag>
          <span v-else>手动创建</span>
        </el-descriptions-item>
        <el-descriptions-item label="父任务">{{ task.parent_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ task.description || '-' }}</el-descriptions-item>
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
        <el-button type="warning" :disabled="!canEdit" @click="handleDecompose">拆解任务</el-button>
        <el-button
          v-if="task.source_type === 'document' && task.source_id"
          type="info"
          @click="emit('open-source', task)"
        >
          查看来源文档
        </el-button>
        <el-button
          v-if="task.source_type === 'legal_consultation' && task.source_id && task.case_id"
          type="info"
          @click="emit('open-source', task)"
        >
          查看来源咨询
        </el-button>
        <el-button v-if="task.status !== 'in_progress'" type="primary" :disabled="!canCollaborate" @click="emit('status-change', task, 'in_progress')">开始执行</el-button>
        <el-button v-if="task.status !== 'done'" type="success" :disabled="!canCollaborate" @click="emit('status-change', task, 'done')">标记完成</el-button>
        <el-button v-if="canEdit && !task.archived_at" type="info" plain @click="emit('archive', task)">归档</el-button>
        <el-button v-if="canEdit && task.archived_at" type="info" plain @click="emit('unarchive', task)">恢复</el-button>
      </div>
      <div v-if="!canEdit" class="readonly-note app-readonly-banner">
        {{ canCollaborate
          ? `你是任务${collaborationRoleLabel}，可更新状态/进度和评论；标题、截止日期、负责人与归档由创建人维护`
          : '共享任务当前仅支持查看，更新、评论和状态变更需由创建人或负责人操作' }}
      </div>

      <div class="detail-section">
        <h4>协作更新</h4>
        <div class="editor-row">
          <label class="date-field">
            <span>截止日期</span>
            <input v-model="dueDraft" type="date" class="date-input" :disabled="!canEdit" aria-label="截止日期" />
          </label>
          <button v-if="dueDraft" type="button" class="clear-date" :disabled="!canEdit" @click="dueDraft = ''">清除日期</button>
        </div>
        <div v-if="canEdit && members.length" class="editor-row">
          <span class="date-label">负责人</span>
          <el-select v-model="assigneeDraft" clearable filterable placeholder="未指派" style="width: 240px">
            <el-option
              v-for="member in members"
              :key="member.user_id"
              :label="member.full_name || member.username || `用户 ${member.user_id}`"
              :value="member.user_id"
            />
          </el-select>
        </div>
        <div class="editor-row">
          <el-input v-model.number="progressDraft" :disabled="!canCollaborate" type="number" style="width: 120px" placeholder="进度 %" />
          <el-select
            v-if="members.length"
            v-model="collaboratorIdsDraft"
            multiple
            filterable
            collapse-tags
            :disabled="!canEdit"
            placeholder="协作者"
            style="width: 260px"
          >
            <el-option
              v-for="member in members"
              :key="member.user_id"
              :label="member.full_name || member.username || `用户 ${member.user_id}`"
              :value="member.user_id"
            />
          </el-select>
          <el-input v-else v-model="collaboratorsDraft" :disabled="!canEdit" placeholder="协作者，逗号分隔" style="width: 260px" />
          <el-button type="primary" :disabled="!canCollaborate" @click="updateCollaboration">保存协作信息</el-button>
        </div>
        <p v-if="canEdit && !collaboratorIdsDraft.length && task.collaborators?.length" class="inline-hint">
          历史协作者：{{ task.collaborators.join('、') }}（重新选择后替换为真实用户）
        </p>
      </div>

      <div class="detail-section">
        <h4>评论</h4>
        <div class="editor-row editor-row-top">
          <el-input v-model="commentDraft" :disabled="!canCollaborate" type="textarea" :rows="2" placeholder="记录进展、阻塞项或协作说明" style="flex: 1" />
          <el-button type="primary" :disabled="!canCollaborate" @click="submitComment">添加评论</el-button>
        </div>
        <div v-if="comments.length" class="comment-list">
          <div v-for="item in comments" :key="`comment-${item.id}`" class="comment-item">
            <div class="comment-head">
              <div class="comment-meta">{{ item.full_name || item.username || `用户 ${item.user_id}` }} · {{ item.created_at }}</div>
              <span v-if="canManageComment(item)" class="comment-actions">
                <el-button link size="small" @click="startEditComment(item)">编辑</el-button>
                <el-button link size="small" type="danger" @click="removeComment(item)">删除</el-button>
              </span>
            </div>
            <div v-if="editingCommentId === item.id" class="comment-edit">
              <el-input v-model="editCommentDraft" type="textarea" :rows="2" placeholder="修改评论" />
              <div class="comment-edit-actions">
                <el-button size="small" type="primary" :loading="commentSaving" @click="saveCommentEdit">保存</el-button>
                <el-button size="small" @click="cancelEditComment">取消</el-button>
              </div>
            </div>
            <div v-else class="comment-content">{{ item.content }}</div>
          </div>
        </div>
      </div>

      <div v-if="taskLogs.length || logActionFilter" class="detail-section">
        <h4>操作日志</h4>
        <div class="log-toolbar">
          <el-select v-model="logActionFilter" size="small" style="width: 150px" @change="handleLogFilterChange">
            <el-option v-for="item in logActionOptions" :key="item.value" :label="item.label" :value="item.value" />
          </el-select>
          <el-button v-if="logHasMore" size="small" :loading="logLoadingMore" @click="loadMoreLogs">加载更多</el-button>
        </div>
        <el-table v-if="taskLogs.length" :data="taskLogs" border size="small" class="detail-table">
          <el-table-column label="操作人" width="120">
            <template #default="{ row }">{{ row.full_name || row.username || (row.user_id ? `用户 ${row.user_id}` : '系统') }}</template>
          </el-table-column>
          <el-table-column label="动作" width="110">
            <template #default="{ row }">{{ taskLogActionLabel(row.action) }}</template>
          </el-table-column>
          <el-table-column label="详情" show-overflow-tooltip>
            <template #default="{ row }">{{ taskLogDetail(row.action, row.detail) }}</template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间" width="180" />
        </el-table>
        <div v-else class="readonly-note">当前筛选下暂无日志</div>
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
            <el-button size="small" text type="primary" @click="emit('open-agent-run', item.id)">查看执行</el-button>
          </div>
        </div>
        <div v-else class="readonly-note app-state-banner">暂无关联 Agent 执行记录</div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import StatusTag from '../StatusTag.vue'
import { taskSourceLabel } from '../../utils/taskSource'
import { taskLogActionLabel, taskLogDetail } from '../../utils/taskLog'
import { useAuthStore } from '../../stores/auth'

const props = defineProps({
  task: { type: Object, default: null },
  visible: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
  canCollaborate: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'refresh', 'task-updated', 'status-change', 'decompose', 'open-source', 'open-case', 'open-agent-run', 'archive', 'unarchive'])

const authStore = useAuthStore()
const currentUser = () => authStore.user
const collaborationRoleLabel = computed(() => (
  props.task?.user_id === currentUser()?.id
    ? '创建人'
    : props.task?.assignee_user_id === currentUser()?.id ? '负责人' : '协作者'
))

const priorityLabelMap = { high: '高', medium: '中', low: '低' }

const subTasks = ref([])
const comments = ref([])
const taskLogs = ref([])
const logActionFilter = ref('')
const logOffset = ref(0)
const logHasMore = ref(false)
const logLoadingMore = ref(false)
const logActionOptions = [
  { label: '全部动作', value: '' },
  { label: '创建任务', value: 'created' },
  { label: '状态变更', value: 'status_changed' },
  { label: '进度更新', value: 'progress_changed' },
  { label: '添加评论', value: 'comment_added' },
  { label: '编辑评论', value: 'comment_updated' },
  { label: '删除评论', value: 'comment_deleted' },
  { label: '负责人变更', value: 'assignee_changed' },
  { label: '优先级变更', value: 'priority_changed' },
  { label: '归档任务', value: 'archived' },
  { label: '恢复任务', value: 'unarchived' },
]
const LOG_PAGE_SIZE = 20
const relatedAgentRuns = ref([])
const commentDraft = ref('')
const editingCommentId = ref(null)
const editCommentDraft = ref('')
const commentSaving = ref(false)
const progressDraft = ref(0)
const collaboratorsDraft = ref('')
const collaboratorIdsDraft = ref([])
const initialCollaboratorIds = ref([])
const dueDraft = ref('')
const initialDueDraft = ref('')
const members = ref([])
const assigneeDraft = ref(null)
const initialAssigneeDraft = ref(null)

const loadMembers = async () => {
  const orgId = currentUser()?.organization_id
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

const formatDueDate = (value) => (value ? String(value).slice(0, 10) : '')
const isOverdue = computed(() => {
  const task = props.task
  if (!task?.due_date || task.status === 'done' || task.status === 'cancelled') return false
  const dueTime = new Date(task.due_date).getTime()
  return Number.isFinite(dueTime) && dueTime < Date.now()
})

const scopeLabel = (item) => {
  const user = currentUser()
  if (!item || !user) return '我的'
  if (item.user_id === user.id) return '我的'
  if (item.assignee_user_id === user.id) return '我负责'
  if (item.department_id && user.department_id && item.department_id === user.department_id) return '部门共享'
  if (item.organization_id && user.organization_id && item.organization_id === user.organization_id) return '组织共享'
  return '共享'
}

const scopeTagType = (item) => {
  const label = scopeLabel(item)
  if (label === '我的') return 'info'
  if (label === '我负责') return 'primary'
  if (label === '部门共享') return 'warning'
  if (label === '组织共享') return 'success'
  return ''
}

const loadSubData = async (taskId) => {
  await Promise.all([loadSubTasks(taskId), loadComments(taskId), loadTaskLogs(taskId), loadRelatedAgentRuns(taskId)])
}

const loadSubTasks = async (taskId) => {
  try {
    const { data } = await api.getSubTasks(taskId)
    if (props.task?.id !== taskId) return
    subTasks.value = data?.items || []
  } catch {
    if (props.task?.id !== taskId) return
    subTasks.value = []
  }
}

const loadComments = async (taskId) => {
  try {
    const { data } = await api.listTaskComments(taskId)
    if (props.task?.id !== taskId) return
    comments.value = data || []
  } catch {
    if (props.task?.id !== taskId) return
    comments.value = []
  }
}

const loadTaskLogs = async (taskId, { append = false } = {}) => {
  try {
    const offset = append ? logOffset.value : 0
    const { data } = await api.listTaskLogs(taskId, {
      limit: LOG_PAGE_SIZE,
      offset,
      order: 'desc',
      action: logActionFilter.value || undefined,
    })
    if (props.task?.id !== taskId) return
    const rows = Array.isArray(data) ? data : []
    taskLogs.value = append ? [...taskLogs.value, ...rows] : rows
    logOffset.value = offset + rows.length
    logHasMore.value = rows.length === LOG_PAGE_SIZE
  } catch {
    if (props.task?.id !== taskId) return
    if (!append) taskLogs.value = []
    logHasMore.value = false
  }
}

const handleLogFilterChange = async () => {
  if (!props.task) return
  logOffset.value = 0
  logHasMore.value = false
  await loadTaskLogs(props.task.id)
}

const loadMoreLogs = async () => {
  if (!props.task || logLoadingMore.value) return
  logLoadingMore.value = true
  try {
    await loadTaskLogs(props.task.id, { append: true })
  } finally {
    logLoadingMore.value = false
  }
}

const loadRelatedAgentRuns = async (taskId) => {
  try {
    const { data } = await api.listAgentRuns({ artifact_type: 'task', artifact_id: taskId, page: 1, page_size: 5 })
    if (props.task?.id !== taskId) return
    relatedAgentRuns.value = data?.items || []
  } catch {
    if (props.task?.id !== taskId) return
    relatedAgentRuns.value = []
  }
}

const updateCollaboration = async () => {
  if (!props.task) return
  const payload = { progress: progressDraft.value }
  if (props.canEdit) {
    const idsChanged = JSON.stringify(collaboratorIdsDraft.value) !== JSON.stringify(initialCollaboratorIds.value)
    if (idsChanged) {
      payload.collaborator_user_ids = [...collaboratorIdsDraft.value]
    } else if (!initialCollaboratorIds.value.length) {
      payload.collaborators = collaboratorsDraft.value
        ? collaboratorsDraft.value.split(',').map((item) => item.trim()).filter(Boolean)
        : []
    }
    // 只在用户改过日期时提交，避免「只改进度」把原截止时间改写成当天 23:59
    if (dueDraft.value !== initialDueDraft.value) {
      payload.due_date = dueDraft.value ? `${dueDraft.value}T23:59:59` : null
    }
    if (assigneeDraft.value !== initialAssigneeDraft.value) {
      payload.assignee_user_id = assigneeDraft.value ?? null
    }
  }
  try {
    await api.updateTask(props.task.id, payload)
    ElMessage.success('协作信息已更新')
    const { data } = await api.getTask(props.task.id)
    emit('task-updated', data)
    await loadTaskLogs(props.task.id)
    emit('refresh')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '协作信息更新失败')
  }
}

const submitComment = async () => {
  if (!props.task || !commentDraft.value.trim()) return
  try {
    await api.addTaskComment(props.task.id, { content: commentDraft.value.trim() })
    commentDraft.value = ''
    ElMessage.success('评论已添加')
    await Promise.all([loadComments(props.task.id), loadTaskLogs(props.task.id)])
    emit('refresh')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '添加评论失败')
  }
}

const canManageComment = (item) => Boolean(
  props.canEdit || (item?.user_id && item.user_id === currentUser()?.id)
)

const startEditComment = (item) => {
  editingCommentId.value = item.id
  editCommentDraft.value = item.content
}

const cancelEditComment = () => {
  editingCommentId.value = null
  editCommentDraft.value = ''
}

const saveCommentEdit = async () => {
  if (!props.task || !editingCommentId.value || !editCommentDraft.value.trim()) return
  commentSaving.value = true
  try {
    await api.updateTaskComment(props.task.id, editingCommentId.value, {
      content: editCommentDraft.value.trim(),
    })
    ElMessage.success('评论已更新')
    cancelEditComment()
    await Promise.all([loadComments(props.task.id), loadTaskLogs(props.task.id)])
    emit('refresh')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '修改评论失败')
  } finally {
    commentSaving.value = false
  }
}

const removeComment = async (item) => {
  if (!props.task) return
  try {
    await ElMessageBox.confirm('删除这条评论？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await api.deleteTaskComment(props.task.id, item.id)
    if (editingCommentId.value === item.id) cancelEditComment()
    ElMessage.success('评论已删除')
    await Promise.all([loadComments(props.task.id), loadTaskLogs(props.task.id)])
    emit('refresh')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '删除评论失败')
  }
}

const handleDecompose = () => {
  emit('decompose', props.task)
  emit('update:visible', false)
}

watch(
  () => props.task,
  (task) => {
    if (!task) return
    progressDraft.value = task.progress || 0
    collaboratorsDraft.value = (task.collaborators || []).join(', ')
    collaboratorIdsDraft.value = [...(task.collaborator_user_ids || [])]
    initialCollaboratorIds.value = [...(task.collaborator_user_ids || [])]
    dueDraft.value = formatDueDate(task.due_date)
    initialDueDraft.value = dueDraft.value
    assigneeDraft.value = task.assignee_user_id ?? null
    initialAssigneeDraft.value = assigneeDraft.value
    commentDraft.value = ''
    editingCommentId.value = null
    editCommentDraft.value = ''
    logActionFilter.value = ''
    logOffset.value = 0
    logHasMore.value = false
    if (props.canEdit) loadMembers()
    loadSubData(task.id)
  },
  { immediate: true },
)

watch(
  () => props.visible,
  (visible) => {
    if (!visible) {
      subTasks.value = []
      comments.value = []
      taskLogs.value = []
      relatedAgentRuns.value = []
      commentDraft.value = ''
    }
  },
)

defineExpose({
  reloadSubTasks(taskId) {
    loadSubTasks(taskId)
  },
})
</script>

<style scoped>
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
.readonly-note {
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
.case-entry {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
}
.case-entry-name {
  color: var(--color-text);
}
.case-entry-link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-primary);
  font: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
}
.case-entry-link:hover {
  color: var(--color-primary-hover);
  text-decoration: underline;
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
.agent-run-top {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.date-field {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.date-input {
  height: 32px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
}
.date-input:focus {
  outline: none;
  border-color: var(--color-primary);
}
.clear-date {
  padding: 0;
  border: 0;
  background: none;
  color: var(--color-primary);
  font: inherit;
  font-size: var(--text-sm);
  cursor: pointer;
}
.clear-date:disabled {
  color: var(--color-text-secondary);
  cursor: not-allowed;
}
.is-overdue-text {
  color: var(--color-danger);
  font-weight: 600;
}
.editor-row-top {
  align-items: flex-start;
}
.comment-list,
.agent-run-list {
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
.comment-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.comment-actions {
  flex-shrink: 0;
}
.comment-edit {
  margin-top: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.comment-edit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.log-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.task-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
.task-dialog :deep(.el-dialog__body) {
  padding-top: var(--space-2);
}
@media (max-width: 760px) {
  .detail-metrics {
    grid-template-columns: 1fr;
  }
}
</style>
