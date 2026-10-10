<template>
  <div ref="bellRef" class="notification-bell">
    <button class="bell-pill" title="通知" @click.stop="toggle">
      <el-icon :size="16"><Bell /></el-icon>
      <span v-if="unread > 0" class="bell-badge">{{ unread > 99 ? '99+' : unread }}</span>
    </button>
    <transition name="bell-fade">
      <div v-if="open" class="bell-panel" @click.stop>
        <div class="bell-header">
          <span class="bell-heading">通知</span>
          <div class="bell-header-actions">
            <button class="bell-view-all" type="button" @click="router.push('/notifications'); open = false">查看全部</button>
            <button v-if="unread > 0" class="bell-mark-all" type="button" @click="markAllRead">全部标记已读</button>
          </div>
        </div>
        <div v-if="items.length" class="bell-list">
          <div
            v-for="n in items"
            :key="n.id"
            class="bell-item"
            :class="{ unread: isUnread(n) }"
            role="button"
            tabindex="0"
            :aria-label="`${n.title}，点击查看`"
            :title="isUnread(n) ? '点击标记已读' : ''"
            @keydown.enter.prevent="openNotification(n)"
            @click="openNotification(n)"
          >
            <div class="bell-item-title">{{ n.title }}</div>
            <div v-if="n.body" class="bell-item-body">{{ n.body }}</div>
            <div class="bell-item-time">{{ formatTime(n.created_at) }}</div>
          </div>
        </div>
        <div v-else class="bell-empty">暂无通知</div>
      </div>
    </transition>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../../api'

const open = ref(false)
const items = ref([])
const unread = ref(0)
const bellRef = ref(null)
const router = useRouter()

const isUnread = (n) => n.status === 'delivered' || n.status === 'sent'

const load = async () => {
  try {
    const { data } = await api.getNotifications()
    items.value = data.items || []
    unread.value = data.unread || 0
  } catch (error) {
    // 通知加载失败不影响主界面，但需要可观测（ux-audit M-13 吞错修复）
    console.error('[notification] 通知列表加载失败', error)
  }
}

const toggle = () => {
  open.value = !open.value
  if (open.value) load()
}

const markRead = async (n) => {
  if (!isUnread(n)) return
  try {
    await api.markNotificationRead(n.id)
    n.status = 'read'
    unread.value = Math.max(0, unread.value - 1)
  } catch (error) {
    console.error('[notification] 标记已读失败', error)
    ElMessage.error('标记已读失败，请重试')
  }
}

const openNotification = async (n) => {
  await markRead(n)
  if (n.reference_type === 'workflow_run') {
    const workflowId = n.reference_id ? String(n.reference_id) : undefined
    if (n.case_id) {
      router.push({ path: '/legal-workspace', query: { case_id: String(n.case_id), tab: 'tasks', workflow_id: workflowId } })
    } else {
      router.push({ path: '/tasks', query: { workflow_id: workflowId } })
    }
    return
  }
  // 生成完成通知（咨询/审查/文书）：直达对应结果 tab（ux-audit M-9）；
  // 咨询另带 consultation_id，进入 tab 后自动恢复结果卡（D12 回归发现修复）
  const tabByType = { consultation: 'consultation', contract_review: 'contract', draft: 'draft' }
  const tab = tabByType[n.reference_type]
  if (tab && n.case_id) {
    const query = { case_id: String(n.case_id), tab }
    if (n.reference_type === 'consultation' && n.reference_id) query.consultation_id = String(n.reference_id)
    router.push({ path: '/legal-workspace', query })
    open.value = false
    return
  }
  if (n.case_id) {
    router.push({ path: '/legal-workspace', query: { case_id: String(n.case_id) } })
  }
}

const markAllRead = async () => {
  try {
    await api.markAllNotificationsRead()
    items.value.forEach((n) => { n.status = 'read' })
    unread.value = 0
  } catch (error) {
    console.error('[notification] 全部已读失败', error)
    ElMessage.error('全部已读失败，请重试')
  }
}

const formatTime = (v) => {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

const onDocClick = (e) => {
  if (bellRef.value && !bellRef.value.contains(e.target)) open.value = false
}

// 30s 轮询未读计数（ux-audit M-9）：等待审核结果/生成完成时无需手动刷新。
// 面板打开时跳过——toggle 已即时刷新，避免轮询打断用户阅读列表。
let pollTimer = null
const startPolling = () => {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    if (!open.value) load()
  }, 30000)
}
const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onMounted(() => {
  load()
  startPolling()
  document.addEventListener('click', onDocClick)
})
onUnmounted(() => {
  stopPolling()
  document.removeEventListener('click', onDocClick)
})
</script>

<style scoped>
.notification-bell {
  position: relative;
}

.bell-pill {
  position: relative;
  min-height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: var(--radius-sm);
  border: 0;
  background: transparent;
  box-shadow: none;
  padding: 2px 4px;
  font-size: var(--text-xs);
  font-weight: 400;
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.bell-pill:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.bell-badge {
  position: absolute;
  top: -3px;
  right: -3px;
  min-width: 15px;
  height: 15px;
  padding: 0 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  background: var(--color-danger);
  color: #ffffff;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}

.bell-panel {
  position: absolute;
  left: 100%;
  top: -4px;
  width: 320px;
  z-index: var(--z-topbar, 200);
  background: #ffffff;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md, 10px);
  box-shadow: 0 12px 32px rgba(30, 41, 59, 0.16);
  overflow: hidden;
}

.bell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
}

.bell-heading {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}

.bell-mark-all {
  font-size: var(--text-xs);
  font-weight: 700;
  color: var(--color-primary);
  background: transparent;
  border: 0;
  cursor: pointer;
  padding: 0;
}

.bell-mark-all:hover {
  text-decoration: underline;
}

.bell-list {
  max-height: 320px;
  overflow-y: auto;
}

.bell-item {
  padding: 10px 12px;
  border-bottom: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.bell-item:hover,
.bell-item:focus-visible {
  background: var(--color-primary-light);
  outline: none;
}

.bell-item-title {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text-secondary);
  line-height: 1.5;
  word-break: break-all;
}

.bell-item.unread .bell-item-title {
  color: var(--color-text);
}

.bell-item-time {
  margin-top: 3px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.bell-header-actions { display: flex; align-items: center; gap: 10px; }
.bell-view-all { padding: 0; border: 0; background: transparent; color: var(--color-text-muted); font-size: var(--text-xs); cursor: pointer; }
.bell-view-all:hover { color: var(--color-primary); }

.bell-item-body {
  margin-top: 3px;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.5;
}

.bell-empty {
  padding: 24px 12px;
  text-align: center;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.bell-fade-enter-active,
.bell-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.bell-fade-enter-from,
.bell-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
