<template>
  <section class="legal-page notification-page">
    <header class="legal-page-heading">
      <div>
        <h1>通知中心</h1>
        <p>查看案件动作、审核和关键日期的处理提醒。</p>
      </div>
      <button v-if="unread > 0" class="notification-action" type="button" @click="markAllRead">全部标记已读</button>
    </header>

    <div class="notification-toolbar" role="toolbar" aria-label="通知筛选">
      <label>
        <span>状态</span>
        <select v-model="filters.status" @change="resetAndLoad">
          <option value="all">全部</option>
          <option value="unread">未读</option>
          <option value="read">已读</option>
        </select>
      </label>
      <label>
        <span>类型</span>
        <select v-model="filters.event_type" @change="resetAndLoad">
          <option value="">全部类型</option>
          <option v-for="item in eventTypes" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label class="case-filter">
        <span>案件编号</span>
        <input v-model="filters.case_id" inputmode="numeric" placeholder="可选" @keyup.enter="resetAndLoad">
      </label>
      <button class="notification-refresh" type="button" :disabled="loading" @click="load">刷新</button>
      <span class="notification-summary">未读 {{ unread }} · 共 {{ total }} 条</span>
    </div>

    <div v-if="error" class="notification-error">{{ error }}</div>
    <div v-else-if="loading && !items.length" class="notification-empty">正在加载通知…</div>
    <div v-else-if="!items.length" class="notification-empty">当前筛选下暂无通知。</div>
    <div v-else class="notification-list">
      <article v-for="item in items" :key="item.id" class="notification-row" :class="{ unread: isUnread(item) }">
        <button class="notification-main" type="button" @click="openNotification(item)">
          <span class="notification-row-heading">
            <strong>{{ item.title }}</strong>
            <span>{{ eventLabel(item.event_type) }}</span>
          </span>
          <span v-if="item.body" class="notification-body">{{ item.body }}</span>
          <span class="notification-meta">
            {{ item.case_id ? `案件 ${item.case_id}` : '工作台' }} · {{ formatTime(item.created_at) }}
          </span>
        </button>
        <button v-if="isUnread(item)" class="notification-read" type="button" @click="markRead(item)">标记已读</button>
      </article>
    </div>

    <footer v-if="total > pageSize" class="notification-pagination">
      <button type="button" :disabled="page <= 1 || loading" @click="page -= 1; load()">上一页</button>
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <button type="button" :disabled="page >= pageCount || loading" @click="page += 1; load()">下一页</button>
    </footer>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'

const router = useRouter()
const items = ref([])
const unread = ref(0)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const error = ref('')
const filters = reactive({ status: 'all', event_type: '', case_id: '' })
const eventTypes = [
  { value: 'workflow', label: '案件工作流' },
  { value: 'deadline', label: '关键日期' },
  { value: 'approval', label: '审核' },
  { value: 'portal', label: '客户门户' },
  { value: 'invoice', label: '账单' },
]
const eventLabels = Object.fromEntries(eventTypes.map((item) => [item.value, item.label]))
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const isUnread = (item) => item.status === 'delivered' || item.status === 'sent'
const eventLabel = (value) => eventLabels[value] || value || '系统通知'
const formatTime = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    const params = {
      status: filters.status,
      event_type: filters.event_type || undefined,
      case_id: filters.case_id ? Number(filters.case_id) : undefined,
      page: page.value,
      page_size: pageSize,
    }
    const { data } = await api.getNotifications({ params })
    items.value = data.items || []
    unread.value = Number(data.unread) || 0
    total.value = Number(data.total) || 0
  } catch (err) {
    error.value = err.response?.data?.detail || '通知暂时无法加载'
  } finally {
    loading.value = false
  }
}

const resetAndLoad = () => {
  page.value = 1
  load()
}

const markRead = async (item) => {
  if (!isUnread(item)) return
  try {
    await api.markNotificationRead(item.id)
    item.status = 'read'
    unread.value = Math.max(0, unread.value - 1)
  } catch (err) {
    error.value = err.response?.data?.detail || '标记通知失败'
  }
}

const markAllRead = async () => {
  try {
    await api.markAllNotificationsRead()
    items.value.forEach((item) => { if (isUnread(item)) item.status = 'read' })
    unread.value = 0
  } catch (err) {
    error.value = err.response?.data?.detail || '标记通知失败'
  }
}

const openNotification = async (item) => {
  await markRead(item)
  if (item.reference_type === 'workflow_run') {
    const workflowId = item.reference_id ? String(item.reference_id) : undefined
    if (item.case_id) {
      router.push({ path: '/legal-workspace', query: { case_id: String(item.case_id), tab: 'tasks', workflow_id: workflowId } })
    } else {
      router.push({ path: '/tasks', query: { workflow_id: workflowId } })
    }
  } else if (item.case_id) {
    router.push({ path: '/legal-workspace', query: { case_id: String(item.case_id) } })
  }
}

onMounted(load)
</script>

<style scoped>
.notification-page { padding-bottom: 28px; }
.notification-action, .notification-refresh, .notification-read, .notification-pagination button { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.notification-action { padding: 0; }
.notification-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; padding: 12px 0; border-top: 1px solid var(--color-border); border-bottom: 1px solid var(--color-border); }
.notification-toolbar label { display: grid; gap: 4px; color: var(--color-text-muted); font-size: 11px; }
.notification-toolbar select, .notification-toolbar input { min-width: 124px; height: 30px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: #fff; color: var(--color-text); font: inherit; font-size: 12px; }
.notification-toolbar input { width: 100px; min-width: 100px; }
.notification-refresh { height: 30px; padding: 0 4px; }.notification-refresh:disabled { color: var(--color-text-muted); cursor: wait; }
.notification-summary { margin-left: auto; color: var(--color-text-muted); font-size: 11px; }
.notification-list { display: grid; }
.notification-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; min-height: 74px; border-bottom: 1px solid var(--color-border-light); }
.notification-row.unread { border-left: 3px solid var(--color-primary); padding-left: 10px; }
.notification-main { display: grid; gap: 5px; min-width: 0; padding: 12px 0; border: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.notification-main:hover { background: #F8FAFB; }
.notification-row-heading { display: flex; align-items: baseline; gap: 10px; min-width: 0; }.notification-row-heading strong { overflow: hidden; font-size: 13px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }.notification-row-heading span { flex: 0 0 auto; color: var(--color-text-muted); font-size: 11px; }
.notification-body, .notification-meta { overflow: hidden; color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }.notification-meta { color: var(--color-text-muted); font-size: 11px; }
.notification-read { padding: 4px 0; white-space: nowrap; }.notification-error, .notification-empty { padding: 24px 0; color: var(--color-text-muted); font-size: 13px; }.notification-error { color: var(--color-danger); }
.notification-pagination { display: flex; justify-content: center; align-items: center; gap: 16px; padding-top: 18px; color: var(--color-text-muted); font-size: 12px; }.notification-pagination button:disabled { color: var(--color-text-muted); cursor: not-allowed; }
@media (max-width: 700px) { .notification-summary { width: 100%; margin-left: 0; }.notification-row { gap: 8px; }.notification-row-heading { display: grid; gap: 2px; }.notification-body { white-space: normal; }.notification-toolbar { align-items: stretch; }.notification-refresh { align-self: end; } }
</style>
