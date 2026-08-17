<template>
  <el-dialog :model-value="visible" title="任务详情" width="720px" class="task-dialog" @update:model-value="emit('update:visible', $event)">
    <template v-if="task">
      <div class="detail-metrics">
        <div class="detail-metric">
          <span>当前状态</span>
          <strong>{{ task.title }}</strong>
          <StatusTag kind="task" :status="task.status" size="small" />
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
        <el-descriptions-item label="来源">
          <el-tag v-if="task.source_type" size="small" type="info">
            {{ sourceTypeLabelMap[task.source_type] || task.source_type }}
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
        <el-button v-if="task.status !== 'in_progress'" type="primary" :disabled="!canEdit" @click="emit('status-change', task, 'in_progress')">开始执行</el-button>
        <el-button v-if="task.status !== 'done'" type="success" :disabled="!canEdit" @click="emit('status-change', task, 'done')">标记完成</el-button>
      </div>
      <div v-if="!canEdit" class="readonly-note app-readonly-banner">
        共享任务当前仅支持查看，更新、评论和状态变更需由创建人操作
      </div>

      <div class="detail-section">
        <h4>协作更新</h4>
        <div class="editor-row">
          <el-input v-model.number="progressDraft" :disabled="!canEdit" type="number" style="width: 120px" placeholder="进度 %" />
          <el-input v-model="collaboratorsDraft" :disabled="!canEdit" placeholder="协作者，逗号分隔" style="width: 260px" />
          <el-button type="primary" :disabled="!canEdit" @click="updateCollaboration">保存协作信息</el-button>
        </div>
      </div>

      <div class="detail-section">
        <h4>评论</h4>
        <div class="editor-row editor-row-top">
          <el-input v-model="commentDraft" :disabled="!canEdit" type="textarea" :rows="2" placeholder="记录进展、阻塞项或协作说明" style="flex: 1" />
          <el-button type="primary" :disabled="!canEdit" @click="submitComment">添加评论</el-button>
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
            <el-button size="small" text type="primary" @click="emit('open-agent-run', item.id)">查看执行</el-button>
          </div>
        </div>
        <div v-else class="readonly-note app-state-banner">暂无关联 Agent 执行记录</div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { ElMessage } from 'element-plus/es/components/message/index'
import StatusTag from '../StatusTag.vue'
import { useAuthStore } from '../../stores/auth'

const props = defineProps({
  task: { type: Object, default: null },
  visible: { type: Boolean, default: false },
  canEdit: { type: Boolean, default: false },
})
const emit = defineEmits(['update:visible', 'refresh', 'task-updated', 'status-change', 'decompose', 'open-source', 'open-agent-run'])

const authStore = useAuthStore()
const currentUser = () => authStore.user

const priorityLabelMap = { high: '高', medium: '中', low: '低' }
const sourceTypeLabelMap = { document: '文档', chat: '聊天', decompose: '拆解' }

const subTasks = ref([])
const comments = ref([])
const taskLogs = ref([])
const relatedAgentRuns = ref([])
const commentDraft = ref('')
const progressDraft = ref(0)
const collaboratorsDraft = ref('')

const scopeLabel = (item) => {
  const user = currentUser()
  if (!item || !user) return '我的'
  if (item.user_id === user.id) return '我的'
  if (item.department_id && user.department_id && item.department_id === user.department_id) return '部门共享'
  if (item.organization_id && user.organization_id && item.organization_id === user.organization_id) return '组织共享'
  return '共享'
}

const scopeTagType = (item) => {
  const label = scopeLabel(item)
  if (label === '我的') return 'info'
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

const loadTaskLogs = async (taskId) => {
  try {
    const { data } = await api.listTaskLogs(taskId)
    if (props.task?.id !== taskId) return
    taskLogs.value = data || []
  } catch {
    if (props.task?.id !== taskId) return
    taskLogs.value = []
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
  try {
    await api.updateTask(props.task.id, {
      progress: progressDraft.value,
      collaborators: collaboratorsDraft.value
        ? collaboratorsDraft.value.split(',').map((item) => item.trim()).filter(Boolean)
        : [],
    })
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
    commentDraft.value = ''
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
