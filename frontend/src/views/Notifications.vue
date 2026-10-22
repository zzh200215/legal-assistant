<template>
  <section class="legal-page notification-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">通知中心</h1>
        <p class="app-page-sub">查看案件动作、审核和关键日期的处理提醒。</p>
      </div>
      <div class="app-page-head-actions">
        <button v-if="unread > 0" class="notification-action" type="button" @click="markAllRead">全部标记已读</button>
      </div>
    </header>

    <section class="app-card">
      <div class="app-card-head">
        <h2>通知</h2>
        <span class="app-card-head-meta">未读 {{ unread }} · 共 {{ total }} 条</span>
      </div>
      <div class="app-card-body">
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
        </div>

        <QueryStateView :status="listStatus" :error="loadError" empty-text="当前筛选下暂无通知。" min-height="0px" @retry="load">
          <div class="notification-list">
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
              <div class="notification-actions">
                <button v-if="canRequestEvidence(item)" class="notification-request" type="button" :disabled="returning[item.id]" @click="requestEvidence(item)">
                  {{ returning[item.id] ? '退回中…' : '要求补充依据' }}
                </button>
                <button v-if="isUnread(item)" class="notification-read" type="button" @click="markRead(item)">标记已读</button>
              </div>
            </article>
          </div>
        </QueryStateView>
      </div>
    </section>

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
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../api'
import QueryStateView from '../components/QueryStateView.vue'
import { normalizeError, errorMessage } from '../api/errors.js'

const router = useRouter()
const items = ref([])
const unread = ref(0)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const loadError = ref(null)
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

// 三态（ux-audit P1-3/M-13）：加载失败与"暂无通知"分离，失败可重试；刷新时保留已有列表不闪骨架
const listStatus = computed(() => {
  if (loading.value && !items.value.length) return 'loading'
  if (loadError.value) return 'error'
  if (!items.value.length) return 'empty'
  return 'success'
})

const load = async () => {
  loading.value = true
  loadError.value = null
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
    loadError.value = normalizeError(err)
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
    ElMessage.error(errorMessage(err))
  }
}

const markAllRead = async () => {
  try {
    await api.markAllNotificationsRead()
    items.value.forEach((item) => { if (isUnread(item)) item.status = 'read' })
    unread.value = 0
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

// 无依据咨询通知：一键退回（要求提交人补充法源依据）
const returning = ref({})
const canRequestEvidence = (item) => (
  item.reference_type === 'consultation' && item.title === '无依据咨询待审核' && !item.returned
)
const requestEvidence = async (item) => {
  if (!item.reference_id) return
  returning.value = { ...returning.value, [item.id]: true }
  try {
    await api.submitLegalReviewAction('consultation', item.reference_id, {
      action: 'return',
      note: '无依据咨询：请补充法源依据后重新提交审核',
    })
    item.returned = true
    await markRead(item)
    ElMessage.success('已退回，要求补充依据')
  } catch (err) {
    ElMessage.error(errorMessage(err))
  } finally {
    returning.value = { ...returning.value, [item.id]: false }
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
    const query = { case_id: String(item.case_id) }
    if (item.reference_type === 'consultation' && item.reference_id) {
      query.tab = 'consultation'
      query.consultation_id = String(item.reference_id)
    }
    router.push({ path: '/legal-workspace', query })
  }
}

onMounted(load)
</script>

<style scoped>
/* 页头与卡片骨架走 design-tokens.css 的 .app-page-head / .app-card，这里只补本页特有的行与工具条 */
.notification-page { display: grid; gap: var(--space-5); padding-bottom: var(--space-8); }
.notification-action, .notification-refresh, .notification-read, .notification-pagination button { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-sm); font-weight: 500; cursor: pointer; }
.notification-action { padding: 0; }
.notification-action:hover, .notification-refresh:hover:not(:disabled), .notification-read:hover { color: var(--color-primary-hover); text-decoration: underline; }
.notification-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: var(--space-3); padding-bottom: var(--space-4); border-bottom: 1px solid var(--color-border-light); }
.notification-toolbar label { display: grid; gap: 4px; color: var(--color-text-muted); font-size: var(--text-xs); }
.notification-toolbar select, .notification-toolbar input { min-width: 124px; height: 32px; padding: 0 var(--space-2); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: var(--text-sm); }
.notification-toolbar select:focus, .notification-toolbar input:focus { border-color: var(--color-primary); outline: 2px solid var(--color-primary-subtle); }
.notification-toolbar input { width: 100px; min-width: 100px; }
.notification-refresh { height: 32px; padding: 0 4px; margin-left: auto; }
.notification-refresh:disabled { color: var(--color-text-muted); cursor: wait; }
.notification-list { display: grid; }
.notification-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-4); align-items: center; min-height: 72px; padding: 0 var(--space-2); border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); transition: background var(--transition-fast); }
.notification-row:last-child { border-bottom: 0; }
/* 未读用左侧主色竖条 + 浅底，读过的行保持中性 */
.notification-row.unread { border-left: 3px solid var(--color-primary); background: var(--color-surface-subtle); }
.notification-row:hover { background: var(--color-surface-hover); }
.notification-main { display: grid; gap: 5px; min-width: 0; padding: var(--space-3) 0; border: 0; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.notification-row-heading { display: flex; align-items: baseline; gap: var(--space-2); min-width: 0; }
.notification-row-heading strong { overflow: hidden; font-size: var(--text-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.notification-row-heading span { flex: 0 0 auto; color: var(--color-text-muted); font-size: var(--text-xs); }
.notification-body, .notification-meta { overflow: hidden; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.5; text-overflow: ellipsis; white-space: nowrap; }
.notification-meta { color: var(--color-text-muted); font-size: var(--text-xs); }
.notification-read { padding: 4px 0; white-space: nowrap; }
.notification-pagination { display: flex; justify-content: center; align-items: center; gap: var(--space-4); color: var(--color-text-muted); font-size: var(--text-sm); }
.notification-pagination button:disabled { color: var(--color-text-muted); cursor: not-allowed; text-decoration: none; }
@media (max-width: 700px) {
  .notification-row { gap: var(--space-2); }
  .notification-row-heading { display: grid; gap: 2px; }
  .notification-body { white-space: normal; }
  .notification-toolbar { align-items: stretch; }
  .notification-refresh { align-self: end; margin-left: 0; }
}

.notification-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.notification-request { padding: 4px 10px; border: 1px solid var(--color-warning); border-radius: var(--radius-sm); background: transparent; color: var(--color-warning); font-size: var(--text-xs); cursor: pointer; }
.notification-request:hover:not(:disabled) { background: var(--color-warning-light); }
.notification-request:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
