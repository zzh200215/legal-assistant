<template>
  <section class="legal-page notification-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">通知中心</h1>
        <p class="app-page-sub">查看案件动作、审核和关键日期的处理提醒。</p>
      </div>
      <div class="app-page-head-actions">
        <button class="notification-action" type="button" @click="router.push('/settings/notifications')">通知偏好</button>
        <button v-if="unread > 0" class="notification-action" type="button" @click="markAllRead">全部标记已读</button>
      </div>
    </header>

    <section v-if="leaveActive" class="notification-leave" role="status">
      <span class="notification-leave-tag">请假中</span>
      <span class="notification-leave-text">{{ leaveSummary(leave) }}</span>
      <button class="notification-leave-link" type="button" @click="router.push('/settings/notifications')">调整请假</button>
    </section>

    <section v-if="summary.length" class="notification-proxy" aria-label="我代收的对象">
      <div class="notification-proxy-head">
        <strong>我代收的对象</strong>
        <span>{{ filters.days ? `近 ${filters.days} 天` : '全部时间' }} · {{ summary.length }} 人 · 共 {{ summaryTotal }} 条</span>
        <button type="button" @click="proxyOpen = !proxyOpen">{{ proxyOpen ? '收起' : '展开' }}</button>
      </div>
      <div v-if="proxyOpen" class="notification-proxy-list">
        <div
          v-for="row in summary"
          :key="row.user_id"
          class="notification-proxy-row"
          :class="{ active: filters.delegated_from === String(row.user_id) }"
        >
          <button type="button" class="notification-proxy-pick" @click="filterByOrigin(row)">
            <span class="notification-proxy-name">{{ row.name || `用户 ${row.user_id}` }}</span>
            <span class="notification-proxy-count">代收 {{ row.count }} 条<template v-if="row.unread"> · 未读 {{ row.unread }}</template></span>
            <span class="notification-proxy-time">{{ formatTime(row.last_at) }}</span>
          </button>
          <button type="button" class="notification-proxy-detail" @click="openOriginDetail(row)">明细</button>
        </div>
      </div>
    </section>

    <section ref="listCard" class="app-card">
      <div class="app-card-head">
        <h2>通知</h2>
        <span class="app-card-head-meta">未读 {{ unread }} · 共 {{ total }} 条</span>
      </div>
      <div class="app-card-body">
        <div v-if="filters.delegated_from" class="notification-proxy-active">
          <span>正在看 <strong>{{ originName }}</strong> 的代收，共 {{ total }} 条{{ filters.days ? `（近 ${filters.days} 天）` : '' }}</span>
          <button type="button" @click="clearOrigin">返回全部</button>
        </div>
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
          <label>
            <span>来源</span>
            <select v-model="filters.delegated" @change="resetAndLoad">
              <option value="">全部来源</option>
              <option value="own">本人</option>
              <option value="delegated">全部代收</option>
              <option value="leave">请假代收</option>
              <option value="event">类型代收</option>
            </select>
          </label>
          <label class="case-filter">
            <span>案件编号</span>
            <input v-model="filters.case_id" inputmode="numeric" placeholder="可选" @keyup.enter="resetAndLoad">
          </label>
          <label>
            <span>时间</span>
            <select v-model="filters.days" @change="changeDays">
              <option value="">全部时间</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
            </select>
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
                  <span
                    v-if="item.on_behalf_of_user_id"
                    class="notification-delegated"
                    :class="`source-${item.on_behalf_of_source || 'unknown'}`"
                  >
                    {{ delegationBadge(item) }}
                  </span>
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
                <button v-if="canGoSupplement(item)" class="notification-request" type="button" @click="goSupplement(item)">去补充</button>
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
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../api'
import QueryStateView from '../components/QueryStateView.vue'
import { normalizeError, errorMessage } from '../api/errors.js'
import { delegationBadge, delegationFilterParams } from '../utils/notificationDelegation'
import { leaveSummary } from '../utils/notificationLeave'

const router = useRouter()
const items = ref([])
const unread = ref(0)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const loading = ref(false)
const loadError = ref(null)
// 请假提示条：只做提示，读取失败按「没有请假」处理，不影响列表
const leave = ref(null)
const leaveActive = computed(() => Boolean(leave.value?.delegate_user_id) && leave.value?.status === 'active')
// 「我代收了谁」：按原收件人分组，点分组即筛到这位同事的通知
const summary = ref([])
const proxyOpen = ref(true)
const listCard = ref(null)
const summaryTotal = computed(() => summary.value.reduce((total, row) => total + (row.count || 0), 0))
const filters = reactive({ status: 'all', event_type: '', case_id: '', delegated: '', delegated_from: '', days: '' })
const originName = computed(() => {
  const row = summary.value.find((item) => String(item.user_id) === filters.delegated_from)
  return row?.name || (filters.delegated_from ? `用户 ${filters.delegated_from}` : '')
})
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
      ...delegationFilterParams(filters.delegated),
      delegated_from_user_id: filters.delegated_from ? Number(filters.delegated_from) : undefined,
      days: filters.days ? Number(filters.days) : undefined,
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
    if (item.on_behalf_of_user_id) bumpSummaryUnread(item.on_behalf_of_user_id, -1)
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

const markAllRead = async () => {
  try {
    await api.markAllNotificationsRead()
    items.value.forEach((item) => { if (isUnread(item)) item.status = 'read' })
    unread.value = 0
    summary.value.forEach((row) => { row.unread = 0 })
  } catch (err) {
    ElMessage.error(errorMessage(err))
  }
}

// 无依据咨询通知：一键退回（要求提交人补充法源依据）
const returning = ref({})
const FORWARD_KEYS = ['review_consultation_uncited', 'review_consultation_weak']
const FORWARD_TITLES = ['无依据咨询待审核', '依据偏弱的咨询待审核']
const canRequestEvidence = (item) => (
  item.reference_type === 'consultation'
  && (FORWARD_KEYS.includes(item.template_key) || FORWARD_TITLES.includes(item.title))
  && !item.returned
)
const canGoSupplement = (item) => (
  item.reference_type === 'consultation'
  && (item.template_key === 'consultation_returned_supplement' || item.title === '咨询被退回：需要补充依据')
)
const supplementNote = (item) => (
  item.template_key === 'review_consultation_weak' || item.title === '依据偏弱的咨询待审核'
    ? '依据偏弱：请补充法源依据后重新提交审核'
    : '无依据咨询：请补充法源依据后重新提交审核'
)
const goSupplement = async (item) => {
  await markRead(item)
  if (!item.case_id) return
  router.push({
    path: '/legal-workspace',
    query: {
      case_id: String(item.case_id),
      tab: 'consultation',
      consultation_id: String(item.reference_id || ''),
      supplement: '1',
    },
  })
}
const requestEvidence = async (item) => {
  if (!item.reference_id) return
  returning.value = { ...returning.value, [item.id]: true }
  try {
    await api.submitLegalReviewAction('consultation', item.reference_id, {
      action: 'return',
      note: supplementNote(item),
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
  if (item.reference_type === 'task' && item.reference_id) {
    // 任务到期/协作者提醒：直达任务中心并打开详情；有案件时保留案件上下文
    const query = { taskId: String(item.reference_id) }
    if (item.case_id) query.case_id = String(item.case_id)
    router.push({ path: '/tasks', query })
  } else if (item.reference_type === 'workflow_run') {
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
    if (canGoSupplement(item)) query.supplement = '1'
    router.push({ path: '/legal-workspace', query })
  }
}

const loadLeave = async () => {
  try {
    const { data } = await api.getNotificationLeave()
    leave.value = data || null
  } catch {
    leave.value = null
  }
}

const loadSummary = async () => {
  try {
    const { data } = await api.getDelegatedSummary({
      params: { days: filters.days ? Number(filters.days) : undefined },
    })
    summary.value = data?.items || []
  } catch {
    summary.value = []
  }
}

// 时间窗同时作用于列表与汇总面板，两处数字始终对得上
const changeDays = () => {
  resetAndLoad()
  loadSummary()
}

const filterByOrigin = (row) => {
  filters.delegated_from = String(row.user_id)
  resetAndLoad()
}

// 明细：清掉其它筛选，只留这位同事 + 当前时间窗，并把列表带到视野里
const openOriginDetail = (row) => {
  filters.status = 'all'
  filters.event_type = ''
  filters.case_id = ''
  filters.delegated = ''
  filters.delegated_from = String(row.user_id)
  resetAndLoad()
  nextTick(() => {
    listCard.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  })
}

const clearOrigin = () => {
  filters.delegated_from = ''
  resetAndLoad()
}

// 已读数在本地同步，避免每点一次「标记已读」都重拉一次汇总
const bumpSummaryUnread = (originId, delta) => {
  const row = summary.value.find((item) => item.user_id === originId)
  if (row) row.unread = Math.max(0, (row.unread || 0) + delta)
}

onMounted(() => {
  load()
  loadLeave()
  loadSummary()
})
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
.notification-row-heading .notification-delegated { padding: 1px 6px; border-radius: 999px; background: var(--color-primary-subtle); color: var(--color-primary); }
/* 类型代收与历史代收用中性色，只有当前请假代收用主色，扫一眼就能分清来源 */
.notification-row-heading .notification-delegated.source-event,
.notification-row-heading .notification-delegated.source-unknown { background: var(--color-surface-subtle); color: var(--color-text-secondary); }
/* 请假提示条：本人视图顶部提示「这几天的通知由代理代收」并直达通知偏好 */
.notification-leave { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-primary-subtle); }
.notification-leave-tag { padding: 1px 8px; border-radius: 999px; background: var(--color-primary); color: var(--color-surface); font-size: var(--text-xs); font-weight: 600; }
.notification-leave-text { flex: 1 1 240px; min-width: 0; color: var(--color-text); font-size: var(--text-sm); }
.notification-leave-link { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-sm); cursor: pointer; }
.notification-leave-link:hover { text-decoration: underline; }
/* 「我代收了谁」：按原收件人分组的折叠面板，点一行即筛到该同事的通知 */
.notification-proxy { display: grid; gap: var(--space-2); padding: var(--space-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); }
.notification-proxy-head { display: flex; align-items: baseline; gap: var(--space-2); }
.notification-proxy-head strong { font-size: var(--text-sm); font-weight: 600; }
.notification-proxy-head > span { flex: 1; color: var(--color-text-muted); font-size: var(--text-xs); }
.notification-proxy-head button, .notification-proxy-active button { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-xs); cursor: pointer; }
.notification-proxy-head button:hover, .notification-proxy-active button:hover, .notification-proxy-active button:focus-visible { text-decoration: underline; }
.notification-proxy-list { display: grid; gap: 4px; }
.notification-proxy-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-2); align-items: center; padding: 0 var(--space-2) 0 0; border: 1px solid transparent; border-radius: var(--radius-sm); background: var(--color-surface-subtle); }
.notification-proxy-row:hover { background: var(--color-surface-hover); }
.notification-proxy-row.active { border-color: var(--color-primary); background: var(--color-primary-subtle); }
.notification-proxy-pick { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: var(--space-3); align-items: center; padding: var(--space-2) var(--space-3); border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.notification-proxy-detail { padding: 2px 4px; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-xs); white-space: nowrap; cursor: pointer; }
.notification-proxy-detail:hover { text-decoration: underline; }
.notification-proxy-name { overflow: hidden; font-size: var(--text-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.notification-proxy-count, .notification-proxy-time { color: var(--color-text-muted); font-size: var(--text-xs); white-space: nowrap; }
.notification-proxy-active { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-3); padding: var(--space-2) var(--space-3); border-radius: var(--radius-sm); background: var(--color-primary-subtle); color: var(--color-text); font-size: var(--text-sm); }
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
  .notification-proxy-pick { grid-template-columns: minmax(0, 1fr) auto; }
  .notification-proxy-time { display: none; }
}

.notification-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.notification-request { padding: 4px 10px; border: 1px solid var(--color-warning); border-radius: var(--radius-sm); background: transparent; color: var(--color-warning); font-size: var(--text-xs); cursor: pointer; }
.notification-request:hover:not(:disabled) { background: var(--color-warning-light); }
.notification-request:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
