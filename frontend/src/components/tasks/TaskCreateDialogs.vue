<template>
  <el-dialog v-model="showCreateDialog" title="新建任务" width="560px" class="task-dialog">
    <div class="form-grid">
      <div v-if="caseId" class="dialog-copy case-hint">创建后自动归入案件 #{{ caseId }}</div>
      <el-input v-model="newTask.title" placeholder="任务标题" />
      <el-input v-model="newTask.description" type="textarea" :rows="3" placeholder="任务描述（可选）" />
      <el-select
        v-if="members.length"
        v-model="newTask.assigneeUserId"
        clearable
        filterable
        placeholder="选择负责人（可选）"
      >
        <el-option
          v-for="member in members"
          :key="member.user_id"
          :label="member.full_name || member.username || `用户 ${member.user_id}`"
          :value="member.user_id"
        />
      </el-select>
      <el-input v-else v-model="newTask.assignee" placeholder="负责人（可选，按姓名）" />
      <el-input v-model="newTask.collaborators" placeholder="协作者，逗号分隔" />
      <div class="form-inline">
        <label class="date-field">
          <span class="date-label">截止日期</span>
          <input v-model="newTask.dueDate" type="date" class="date-input" aria-label="截止日期" />
        </label>
      </div>
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
</template>

<script setup>
import { ref } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/select/style/css'
import api from '../../api'
import { ElMessage } from 'element-plus/es/components/message/index'

const props = defineProps({
  loading: { type: Boolean, default: false },
  caseId: { type: Number, default: null },
  organizationId: { type: Number, default: null },
})
const emit = defineEmits(['refresh'])

const showCreateDialog = ref(false)
const members = ref([])
const newTask = ref({ title: '', description: '', assignee: '', assigneeUserId: null, collaborators: '', priority: 'medium', progress: 0, dueDate: '' })

const showExtractDialog = ref(false)
const extractDocId = ref(null)

const showChatExtractDialog = ref(false)
const chatMessage = ref('')

const resetCreateForm = () => {
  newTask.value = { title: '', description: '', assignee: '', assigneeUserId: null, collaborators: '', priority: 'medium', progress: 0, dueDate: '' }
}

const loadMembers = async () => {
  if (!props.organizationId) {
    members.value = []
    return
  }
  try {
    const { data } = await api.listOrgMembers(props.organizationId)
    members.value = data || []
  } catch {
    members.value = []
  }
}

const createTask = async () => {
  if (!newTask.value.title.trim()) return ElMessage.warning('请输入标题')
  const payload = {
    ...newTask.value,
    collaborators: newTask.value.collaborators
      ? newTask.value.collaborators.split(',').map((item) => item.trim()).filter(Boolean)
      : [],
  }
  if (payload.assigneeUserId) {
    payload.assignee_user_id = payload.assigneeUserId
    delete payload.assignee
  }
  delete payload.assigneeUserId
  // 日期控件只到天：统一落到当天 23:59:59，避免当天 00:00 就被判逾期
  delete payload.dueDate
  if (newTask.value.dueDate) payload.due_date = `${newTask.value.dueDate}T23:59:59`
  // 案件任务视图下新建：默认归入当前案件，避免创建后从筛选结果里消失
  if (props.caseId) payload.case_id = props.caseId
  try {
    await api.createTask(payload)
    ElMessage.success('创建成功')
    showCreateDialog.value = false
    resetCreateForm()
    emit('refresh')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  }
}

const extractFromDoc = async () => {
  if (!extractDocId.value) return ElMessage.warning('请输入文档 ID')
  try {
    const { data } = await api.extractTasksFromDoc(extractDocId.value)
    ElMessage.success(`从文档提取了 ${data.created_tasks} 个任务`)
    showExtractDialog.value = false
    emit('refresh', { resetPage: true })
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '提取失败')
  }
}

const extractFromChat = async () => {
  if (!chatMessage.value.trim()) return ElMessage.warning('请输入消息')
  try {
    const { data } = await api.extractTasksFromChat(chatMessage.value)
    ElMessage.success(`从聊天提取了 ${data.created_tasks} 个任务`)
    showChatExtractDialog.value = false
    chatMessage.value = ''
    emit('refresh', { resetPage: true })
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '提取失败')
  }
}

defineExpose({
  openCreate() {
    resetCreateForm()
    showCreateDialog.value = true
    loadMembers()
  },
  openDocExtract() { showExtractDialog.value = true },
  openChatExtract() { showChatExtractDialog.value = true },
})
</script>

<style scoped>
.task-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
.task-dialog :deep(.el-dialog__body) {
  padding-top: var(--space-2);
}
.form-grid {
  display: grid;
  gap: var(--space-3);
}
.form-inline {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}
.dialog-copy {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
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
.case-hint {
  color: var(--color-primary);
  font-weight: 600;
}
</style>
