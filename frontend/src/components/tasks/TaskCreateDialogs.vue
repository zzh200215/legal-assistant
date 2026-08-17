<template>
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

defineProps({
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['refresh'])

const showCreateDialog = ref(false)
const newTask = ref({ title: '', description: '', assignee: '', collaborators: '', priority: 'medium', progress: 0 })

const showExtractDialog = ref(false)
const extractDocId = ref(null)

const showChatExtractDialog = ref(false)
const chatMessage = ref('')

const resetCreateForm = () => {
  newTask.value = { title: '', description: '', assignee: '', collaborators: '', priority: 'medium', progress: 0 }
}

const createTask = async () => {
  if (!newTask.value.title.trim()) return ElMessage.warning('请输入标题')
  try {
    await api.createTask({
      ...newTask.value,
      collaborators: newTask.value.collaborators
        ? newTask.value.collaborators.split(',').map((item) => item.trim()).filter(Boolean)
        : [],
    })
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
  openCreate() { showCreateDialog.value = true },
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
</style>
