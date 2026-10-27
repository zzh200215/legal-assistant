<template>
  <div class="app-section-intro tab-intro">
    <strong>Outbox 投递看板</strong>
    <span>通知与邮件两条 Outbox 的投递率、当前积压、死信明细；死信可在这里直接重排。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-filter-label">统计窗口：</span>
        <el-radio-group v-model="days" @change="fetchOverview">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <div class="app-empty-note">窗口口径按创建时间；积压与死信是当前值，不受窗口影响。</div>
        <div class="app-filter-bar-actions">
          <el-button :loading="loading" @click="fetchOverview">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div v-if="alerts.length" class="delivery-alerts">
    <div v-for="alert in alerts" :key="alert.code" class="delivery-alert" :class="`is-${alert.level}`">
      <span class="delivery-alert-dot" aria-hidden="true"></span>
      <span>{{ alert.message }}</span>
    </div>
  </div>

  <div class="app-metric-grid-4 delivery-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">通知投递率</span>
      <strong class="app-stat-value">{{ formatRate(overview.notification.rate) }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">通知事件（窗口内）</span>
      <strong class="app-stat-value">{{ overview.notification.total }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">待投递积压</span>
      <strong class="app-stat-value">{{ backlogTotal }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">死信</span>
      <strong class="app-stat-value" :class="{ 'is-warn': deadLetterTotal > 0 }">{{ deadLetterTotal }}</strong>
    </div>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>投递趋势</h2>
      <span class="app-card-head-meta">按创建日分桶 · 最多 30 天 · 悬停看每日明细</span>
    </div>
    <div class="app-card-body">
      <div class="trend-block">
        <div class="trend-head"><strong>通知</strong><span class="app-num">{{ trendSummary(trendNotification) }}</span></div>
        <div class="trend-strip" :class="{ 'is-empty': !trendNotification.length }">
          <div v-for="(day, index) in trendNotification" :key="`n-${day.date}`" class="trend-col" :title="trendTitle(day)">
            <div class="trend-stack">
              <span v-if="day.delivered" class="trend-seg is-delivered" :style="{ flexGrow: day.delivered }"></span>
              <span v-if="day.failed" class="trend-seg is-failed" :style="{ flexGrow: day.failed }"></span>
              <span v-if="day.dead_letter" class="trend-seg is-dead" :style="{ flexGrow: day.dead_letter }"></span>
              <span v-if="day.open" class="trend-seg is-open" :style="{ flexGrow: day.open }"></span>
            </div>
            <span class="trend-label">{{ index % trendLabelStep(trendNotification.length) === 0 ? shortDate(day.date) : '' }}</span>
          </div>
        </div>
      </div>
      <div class="trend-block">
        <div class="trend-head"><strong>邮件</strong><span class="app-num">{{ trendSummary(trendEmail) }}</span></div>
        <div class="trend-strip" :class="{ 'is-empty': !trendEmail.length }">
          <div v-for="(day, index) in trendEmail" :key="`e-${day.date}`" class="trend-col" :title="trendTitle(day)">
            <div class="trend-stack">
              <span v-if="day.delivered" class="trend-seg is-delivered" :style="{ flexGrow: day.delivered }"></span>
              <span v-if="day.failed" class="trend-seg is-failed" :style="{ flexGrow: day.failed }"></span>
              <span v-if="day.dead_letter" class="trend-seg is-dead" :style="{ flexGrow: day.dead_letter }"></span>
              <span v-if="day.open" class="trend-seg is-open" :style="{ flexGrow: day.open }"></span>
            </div>
            <span class="trend-label">{{ index % trendLabelStep(trendEmail.length) === 0 ? shortDate(day.date) : '' }}</span>
          </div>
        </div>
      </div>
      <div class="trend-legend">
        <span><i class="trend-dot is-delivered"></i>已投递</span>
        <span><i class="trend-dot is-failed"></i>失败</span>
        <span><i class="trend-dot is-dead"></i>死信</span>
        <span><i class="trend-dot is-open"></i>未完成</span>
        <span class="trend-note">柱高按当日创建量归一，同一天内按状态占比分段</span>
      </div>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>通知渠道</h2>
      <span class="app-card-head-meta">窗口内 {{ overview.window.days }} 天</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="channelStatus" :error="error" empty-text="窗口内没有通知事件" min-height="160px" @retry="fetchOverview">
          <el-table :data="overview.notification.by_channel" border size="small">
            <el-table-column prop="label" label="渠道" width="70" />
            <el-table-column prop="total" label="总数" width="60">
              <template #default="{ row }"><span class="app-num">{{ row.total }}</span></template>
            </el-table-column>
            <el-table-column prop="delivered" label="已投递" width="66">
              <template #default="{ row }"><span class="app-num">{{ row.delivered }}</span></template>
            </el-table-column>
            <el-table-column prop="failed" label="失败" width="58">
              <template #default="{ row }"><span class="app-num">{{ row.failed }}</span></template>
            </el-table-column>
            <el-table-column prop="dead_letter" label="死信" width="58">
              <template #default="{ row }"><span class="app-num">{{ row.dead_letter }}</span></template>
            </el-table-column>
            <el-table-column prop="pending" label="待投递" width="64">
              <template #default="{ row }"><span class="app-num">{{ row.pending }}</span></template>
            </el-table-column>
            <el-table-column prop="rate" label="成功率" min-width="70">
              <template #default="{ row }"><span class="app-num">{{ formatRate(row.rate) }}</span></template>
            </el-table-column>
          </el-table>
        </QueryStateView>
      </div>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>积压曲线</h2>
      <span class="app-card-head-meta">
        最近 {{ history.hours }} 小时 · 每 5 分钟采样 · 当前 {{ history.snapshot_count }} 个点
      </span>
    </div>
    <div class="app-card-body">
      <div v-if="history.empty" class="delivery-empty">
        还没有积压快照。确认 beat 里的 <code>check_outbox_snapshot</code> 在运行后，这里会显示通知/邮件的积压曲线。
      </div>
      <template v-else>
        <svg class="backlog-chart" viewBox="0 0 600 140" preserveAspectRatio="none" role="img" aria-label="投递积压曲线">
          <line x1="0" y1="132" x2="600" y2="132" class="chart-axis" />
          <polyline :points="chartLine('notification')" class="chart-line is-notification" />
          <polyline :points="chartLine('email')" class="chart-line is-email" />
        </svg>
        <div class="chart-meta">
          <span class="app-num">{{ historyStartLabel }}</span>
          <span class="chart-legend">
            <i class="trend-dot is-notification"></i>通知积压
            <i class="trend-dot is-email"></i>邮件积压
            · 峰值 {{ backlogMax }}
          </span>
          <span class="app-num">{{ historyEndLabel }}</span>
        </div>
        <div class="throughput-row">
          <span>近 {{ history.hours }} 小时吞吐：</span>
          <span class="app-num">通知创建 {{ throughputTotals.notificationCreated }} / 投递 {{ throughputTotals.notificationDelivered }}</span>
          <span class="app-num">邮件创建 {{ throughputTotals.emailCreated }} / 投递 {{ throughputTotals.emailDelivered }}</span>
        </div>
      </template>
    </div>
  </section>

  <div class="app-panel-grid-2 delivery-breakdown-grid">
    <section class="app-card">
      <div class="app-card-head">
        <h2>邮件 Outbox</h2>
        <span class="app-card-head-meta">{{ overview.email.policy_enabled ? '外发已启用' : '外发未启用' }}</span>
      </div>
      <div class="app-card-body">
        <dl class="delivery-kv">
          <div><dt>邮箱投递率</dt><dd class="app-num">{{ formatRate(overview.email.rate) }}</dd></div>
          <div><dt>窗口内请求</dt><dd class="app-num">{{ overview.email.total }}</dd></div>
          <div><dt>24h 已发送</dt><dd class="app-num">{{ overview.email.sent_24h }}</dd></div>
          <div><dt>待审批</dt><dd class="app-num">{{ overview.email.backlog.pending }}</dd></div>
          <div><dt>待投递</dt><dd class="app-num">{{ overview.email.backlog.approved }}</dd></div>
          <div><dt>到期待重试</dt><dd class="app-num">{{ overview.email.backlog.retry_pending }}</dd></div>
          <div><dt>DLP 阻断</dt><dd class="app-num">{{ overview.email.dlp_blocked }}</dd></div>
          <div><dt>卡死租约</dt><dd class="app-num">{{ overview.email.backlog.stale_claims }}</dd></div>
        </dl>
      </div>
    </section>

    <section class="app-card">
      <div class="app-card-head">
        <h2>失败原因</h2>
        <span class="app-card-head-meta">窗口内 failed + dead_letter</span>
      </div>
      <div class="app-card-body">
        <QueryStateView :status="failureStatus" :error="error" empty-text="没有失败记录" min-height="140px" @retry="fetchOverview">
          <ul class="delivery-code-list">
            <li v-for="item in failureCodes" :key="item.code">
              <code>{{ item.code }}</code><span class="app-num">{{ item.count }}</span>
            </li>
          </ul>
        </QueryStateView>
      </div>
    </section>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>投递健康</h2>
      <span class="app-card-head-meta">
        阈值每 15 分钟扫描；同组织同告警冷却 {{ overview.health.cooldown_minutes }} 分钟
      </span>
    </div>
    <div class="app-card-body">
      <ul class="health-list">
        <li v-for="check in overview.health.checks" :key="check.code" class="health-item">
          <span class="health-name">{{ check.label }}</span>
          <span class="health-hint">{{ check.hint }}</span>
          <span class="app-num health-value">{{ check.value }} / 阈值 {{ check.threshold }}</span>
          <span class="app-tag" :class="check.value >= check.threshold ? 'is-danger' : 'is-ok'">
            {{ check.value >= check.threshold ? '超阈值' : '正常' }}
          </span>
        </li>
      </ul>
      <p class="health-note">
        超阈值时由 <code>check_outbox_health</code> 给组织管理员发站内+邮件告警（冷却窗口内不重复）；平台管理员（无组织）只在此页看全局，不接收组织级告警。
      </p>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>死信</h2>
      <span class="app-card-head-meta">通知 + 邮件，最近 {{ deadLetterRows.length }} 条</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="deadLetterStatus" :error="error" empty-text="没有死信，投递链路正常" min-height="160px" @retry="fetchOverview">
          <el-table :data="deadLetterRows" border size="small" max-height="420">
            <el-table-column prop="kindLabel" label="类型" width="84" />
            <el-table-column prop="subject" label="标题 / 主题" min-width="130" show-overflow-tooltip />
            <el-table-column prop="error_code" label="错误码" width="110" show-overflow-tooltip />
            <el-table-column prop="attempt" label="尝试" width="52">
              <template #default="{ row }"><span class="app-num">{{ row.attempt || 0 }}</span></template>
            </el-table-column>
            <el-table-column prop="time" label="进入死信" width="104">
              <template #default="{ row }"><span class="app-num">{{ formatTime(row.time) }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="62">
              <template #default="{ row }">
                <el-button link type="primary" :loading="retrying === `${row.kind}-${row.id}`" @click="retry(row)">重排</el-button>
              </template>
            </el-table-column>
          </el-table>
        </QueryStateView>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import QueryStateView from '../QueryStateView.vue'

const emptyOverview = () => ({
  window: { days: 7, start: null, end: null },
  generated_at: null,
  notification: {
    total: 0,
    rate: null,
    success: 0,
    failed: 0,
    dead_letter: 0,
    by_channel: [],
    by_status: {},
    backlog: { pending: 0, approved: 0, sending: 0, retry_pending: 0, stale_claims: 0, open_total: 0, oldest_pending_age_minutes: null },
    dead_letters: { total: 0, items: [] },
    failure_codes: [],
  },
  email: {
    total: 0,
    rate: null,
    sent: 0,
    sent_24h: 0,
    dead_letter: 0,
    dlp_blocked: 0,
    policy_enabled: false,
    backlog: { pending: 0, approved: 0, sending: 0, retry_pending: 0, stale_claims: 0, open_total: 0, oldest_pending_age_minutes: null },
    dead_letters: { total: 0, items: [] },
    failure_codes: [],
  },
  alerts: [],
  trend: { days: 7, notification: [], email: [], metric: '' },
  health: {
    organization_id: null,
    checks: [],
    breached: [],
    healthy: true,
    cooldown_minutes: 180,
  },
})

const emptyHistory = () => ({
  hours: 24,
  organization_id: null,
  snapshot_count: 0,
  empty: true,
  interval_note: '',
  backlog: [],
  throughput: [],
})

const days = ref(7)
const loading = ref(false)
const overview = ref(emptyOverview())
const history = ref(emptyHistory())
const error = ref(null)
const retrying = ref('')

const alerts = computed(() => overview.value.alerts || [])
const backlogTotal = computed(() =>
  (overview.value.notification.backlog.open_total || 0) + (overview.value.email.backlog.open_total || 0))
const deadLetterTotal = computed(() =>
  (overview.value.notification.dead_letter || 0) + (overview.value.email.dead_letter || 0))

const trendNotification = computed(() => overview.value.trend?.notification || [])
const trendEmail = computed(() => overview.value.trend?.email || [])
const shortDate = (value) => String(value || '').slice(5)

// 积压曲线：SVG 折线，采样到 ≤ 240 点避免 5 分钟粒度在 24h 上画 288 段太密
const backlogMax = computed(() => {
  const values = history.value.backlog.flatMap((point) => [Number(point.notification) || 0, Number(point.email) || 0])
  return Math.max(1, ...values)
})
const chartLine = (key) => {
  const points = history.value.backlog
  if (!points.length) return ''
  const step = Math.max(1, Math.ceil(points.length / 240))
  const sampled = points.filter((_, index) => index % step === 0 || index === points.length - 1)
  return sampled.map((point, index) => {
    const x = sampled.length === 1 ? 300 : (index / (sampled.length - 1)) * 600
    const value = Number(point[key]) || 0
    const y = 132 - (value / backlogMax.value) * 120
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}
const timeLabel = (value) => (value ? String(value).slice(11, 16) : '-')
const historyStartLabel = computed(() => timeLabel(history.value.backlog[0]?.at))
const historyEndLabel = computed(() => timeLabel(history.value.backlog[history.value.backlog.length - 1]?.at))
const throughputTotals = computed(() => history.value.throughput.reduce((acc, hour) => {
  acc.notificationCreated += Number(hour.notification_created) || 0
  acc.notificationDelivered += Number(hour.notification_delivered) || 0
  acc.emailCreated += Number(hour.email_created) || 0
  acc.emailDelivered += Number(hour.email_delivered) || 0
  return acc
}, { notificationCreated: 0, notificationDelivered: 0, emailCreated: 0, emailDelivered: 0 }))
const trendLabelStep = (count) => Math.max(1, Math.ceil((count || 1) / 7))
const trendTitle = (day) => `${day.date} · 创建 ${day.created} · 已投递 ${day.delivered} · 失败 ${day.failed} · 死信 ${day.dead_letter} · 未完成 ${day.open}`
const trendSummary = (series) => {
  const totals = series.reduce((acc, day) => {
    acc.created += Number(day.created) || 0
    acc.delivered += Number(day.delivered) || 0
    acc.dead += Number(day.dead_letter) || 0
    return acc
  }, { created: 0, delivered: 0, dead: 0 })
  if (!totals.created) return '窗口内没有事件'
  const rate = totals.delivered + totals.dead
    ? Math.round((totals.delivered / (totals.delivered + totals.dead)) * 100)
    : null
  return `创建 ${totals.created} · 已投递 ${totals.delivered} · 死信 ${totals.dead}${rate === null ? '' : ` · 完成率 ${rate}%`}`
}

const failureCodes = computed(() => {
  const merged = new Map()
  for (const item of [...(overview.value.notification.failure_codes || []), ...(overview.value.email.failure_codes || [])]) {
    merged.set(item.code, (merged.get(item.code) || 0) + Number(item.count || 0))
  }
  return [...merged.entries()]
    .map(([code, count]) => ({ code, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)
})

// 两类死信合成一张表：kind 决定重排接口；类型列带上渠道（通知·站内 / 通知·邮件 / 邮件）
const deadLetterRows = computed(() => [
  ...(overview.value.notification.dead_letters.items || []).map((row) => ({
    kind: 'notification',
    kindLabel: `通知·${row.channel_label || row.channel}`,
    id: row.id,
    subject: row.title,
    error_code: row.error_code || '-',
    attempt: row.attempt,
    time: row.updated_at,
  })),
  ...(overview.value.email.dead_letters.items || []).map((row) => ({
    kind: 'email',
    kindLabel: '邮件',
    id: row.id,
    subject: row.subject,
    error_code: row.error_code || '-',
    attempt: row.attempt,
    time: row.dead_letter_at,
  })),
].sort((a, b) => String(b.time || '').localeCompare(String(a.time || ''))))

const channelStatus = computed(() => {
  if (loading.value && !overview.value.notification.by_channel.length) return 'loading'
  if (error.value) return 'error'
  if (!overview.value.notification.by_channel.length) return 'empty'
  return 'success'
})
const failureStatus = computed(() => {
  if (loading.value && !failureCodes.value.length) return 'loading'
  if (error.value) return 'error'
  if (!failureCodes.value.length) return 'empty'
  return 'success'
})
const deadLetterStatus = computed(() => {
  if (loading.value && !deadLetterRows.value.length) return 'loading'
  if (error.value) return 'error'
  if (!deadLetterRows.value.length) return 'empty'
  return 'success'
})

const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value) * 100)}%`
}
const formatTime = (value) => {
  if (!value) return '-'
  // 列宽有限：只留「MM-DD HH:mm」，完整时间在接口/tooltip 里
  const text = String(value).replace('T', ' ')
  return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(text) ? text.slice(5, 16) : text
}

const fetchHistory = async () => {
  try {
    const { data } = await api.outboxHistory({ hours: 24 })
    history.value = { ...emptyHistory(), ...(data || {}) }
  } catch {
    // 历史是辅助视图：失败不阻塞看板主数据，也不弹错
    history.value = emptyHistory()
  }
}

const fetchOverview = async () => {
  loading.value = true
  error.value = null
  try {
    const { data } = await api.outboxOverview({ days: days.value })
    overview.value = { ...emptyOverview(), ...(data || {}) }
    await fetchHistory()
  } catch (err) {
    error.value = err
    overview.value = emptyOverview()
    history.value = emptyHistory()
    ElMessage.error(err.response?.data?.message || '获取投递看板失败')
  } finally {
    loading.value = false
  }
}

const retry = async (row) => {
  retrying.value = `${row.kind}-${row.id}`
  try {
    await api.retryOutboxDeadLetter(row.kind, row.id)
    ElMessage.success(row.kind === 'email' ? '已重排为待审批，审批后由邮件 worker 投递' : '已重排为待投递')
    await fetchOverview()
  } catch (err) {
    ElMessage.error(err.response?.data?.message || '重排失败')
  } finally {
    retrying.value = ''
  }
}

onMounted(fetchOverview)
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.delivery-metric-grid,
.delivery-breakdown-grid {
  margin-top: var(--space-4);
}
.delivery-alerts {
  display: grid;
  gap: var(--space-2);
  margin-top: var(--space-4);
}
.delivery-alert {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  font-size: 13px;
  color: var(--color-text-secondary);
}
.delivery-alert-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-muted);
  flex: none;
}
.delivery-alert.is-warning {
  border-color: rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.08);
  color: var(--color-text);
}
.delivery-alert.is-warning .delivery-alert-dot {
  background: #f59e0b;
}
.delivery-kv {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-2) var(--space-4);
  margin: 0;
}
.delivery-kv div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  border-bottom: 1px dashed var(--color-border);
}
.delivery-kv dt {
  font-size: 13px;
  color: var(--color-text-secondary);
}
.delivery-kv dd {
  margin: 0;
  font-weight: 600;
}
.delivery-code-list {
  display: grid;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}
.delivery-code-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  border-bottom: 1px dashed var(--color-border);
}
.delivery-code-list code {
  font-size: 12px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
}
.app-stat-value.is-warn {
  color: #d97706;
}

/* ── 投递趋势（纯 CSS 堆叠柱，不引图表库）── */
.trend-block {
  display: grid;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}
.trend-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: 13px;
}
.trend-strip {
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 76px;
  padding-top: var(--space-1);
  border-bottom: 1px solid var(--color-border);
}
.trend-strip.is-empty {
  opacity: 0.4;
}
.trend-col {
  flex: 1 1 0;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
.trend-stack {
  display: flex;
  flex-direction: column-reverse;
  height: 100%;
  overflow: hidden;
  border-radius: 2px 2px 0 0;
  background: var(--color-bg-alt);
}
.trend-seg {
  display: block;
  width: 100%;
}
.trend-seg.is-delivered,
.trend-dot.is-delivered {
  background: #10b981;
}
.trend-seg.is-failed,
.trend-dot.is-failed {
  background: #f59e0b;
}
.trend-seg.is-dead,
.trend-dot.is-dead {
  background: #ef4444;
}
.trend-seg.is-open,
.trend-dot.is-open {
  background: #94a3b8;
}
.trend-label {
  height: 12px;
  margin-top: 2px;
  font-size: 10px;
  line-height: 12px;
  color: var(--color-text-muted);
  text-align: center;
  white-space: nowrap;
}
.trend-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.trend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 4px;
  border-radius: 2px;
}
.trend-note {
  color: var(--color-text-muted);
}

/* ── 健康检查清单 ── */
.health-list {
  display: grid;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}
.health-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 13px;
}
.health-name {
  width: 110px;
  flex: none;
  font-weight: 600;
  color: var(--color-text);
}
.health-hint {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.health-value {
  white-space: nowrap;
}
.health-note {
  margin: var(--space-3) 0 0;
  font-size: 12px;
  line-height: 18px;
  color: var(--color-text-muted);
}
.health-note code {
  font-size: 11px;
}

/* ── 积压曲线（SVG 折线，无图表库）── */
.backlog-chart {
  width: 100%;
  height: 140px;
  display: block;
}
.chart-axis {
  stroke: var(--color-border);
  stroke-width: 1;
  vector-effect: non-scaling-stroke;
}
.chart-line {
  fill: none;
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
  stroke-linejoin: round;
  stroke-linecap: round;
}
.chart-line.is-notification {
  stroke: #4f46e5;
}
.chart-line.is-email {
  stroke: #10b981;
}
.chart-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-top: var(--space-1);
  font-size: 12px;
  color: var(--color-text-muted);
}
.chart-legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.trend-dot.is-notification {
  background: #4f46e5;
}
.trend-dot.is-email {
  background: #10b981;
}
.throughput-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
  font-size: 12px;
  color: var(--color-text-secondary);
}
.delivery-empty {
  padding: var(--space-4);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
  font-size: 13px;
  line-height: 20px;
  color: var(--color-text-secondary);
}
.delivery-empty code {
  font-size: 12px;
}
</style>
