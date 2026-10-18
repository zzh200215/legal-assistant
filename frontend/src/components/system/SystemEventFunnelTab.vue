<template>
  <div class="event-funnel-tab">
    <div class="app-section-intro tab-intro">
      <strong>产品埋点漏斗（服务端事件）</strong>
      <span>注册 → 登录 → 建案 → 提交咨询 → 提交审核，基于服务端埋点事件计数。各阶段为窗口内去重用户数，非同一队列逐步追踪；转化率仅供流失定位参考。</span>
    </div>

    <section class="app-card">
      <div class="app-card-body">
        <div class="app-filter-bar">
          <span class="app-filter-label">统计窗口：</span>
          <el-radio-group v-model="days" @change="load">
            <el-radio-button :value="7">近 7 天</el-radio-button>
            <el-radio-button :value="30">近 30 天</el-radio-button>
            <el-radio-button :value="90">近 90 天</el-radio-button>
          </el-radio-group>
          <div class="app-filter-bar-actions">
            <el-button :loading="loading" @click="load">刷新</el-button>
          </div>
        </div>
      </div>
    </section>

    <QueryStateView :status="status" :error="error" min-height="200px" @retry="load">
      <div class="app-metric-grid-4 funnel-metric-grid">
        <div class="app-stat">
          <span class="app-stat-label">注册用户</span>
          <strong class="app-stat-value">{{ stepUsers('auth_register') }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">提交咨询用户</span>
          <strong class="app-stat-value">{{ stepUsers('consult_submit') }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">注册→咨询转化(%)</span>
          <strong class="app-stat-value">{{ overallPct('consult_submit') }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">门户访问</span>
          <strong class="app-stat-value">{{ portalVisits }}</strong>
        </div>
      </div>

      <section class="app-card">
        <div class="app-card-head">
          <h2>阶段明细</h2>
          <span class="app-card-head-meta">近 {{ days }} 天 · 共 {{ funnelRows.length }} 个阶段</span>
        </div>
        <div class="app-card-body is-flush">
          <ol class="funnel-steps">
            <li v-for="row in funnelRows" :key="row.label" class="app-thread is-static">
              <span class="app-thread-title">{{ row.label }}</span>
              <span class="app-thread-meta">
                <span class="app-step-metric">
                  <span class="app-step-label">去重用户</span>
                  <span class="app-num">{{ row.users }}</span>
                </span>
                <span class="app-step-metric">
                  <span class="app-step-label">上一跳转化</span>
                  <span class="app-num">{{ row.first ? '—' : `${row.hopPct}%` }}</span>
                </span>
              </span>
            </li>
          </ol>
          <div class="app-table-wrap">
            <el-table v-loading="loading" :data="funnelRows" border size="small">
              <el-table-column prop="label" label="阶段" width="150" />
              <el-table-column prop="users" label="去重用户" width="100" class-name="app-num" />
              <el-table-column prop="total" label="事件量" width="90" class-name="app-num" />
              <el-table-column label="占注册比例" width="120">
                <template #default="{ row }"><span class="app-num">{{ row.overallPct }}%</span></template>
              </el-table-column>
              <el-table-column label="上一跳转化" width="120">
                <template #default="{ row }">
                  <span v-if="row.first">—</span>
                  <span v-else class="app-num">{{ row.hopPct }}%</span>
                </template>
              </el-table-column>
              <el-table-column label="相对占比">
                <template #default="{ row }"><span class="app-num">{{ row.width }}%</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </section>
    </QueryStateView>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/button/style/css'
import api from '../../api'
import QueryStateView from '../QueryStateView.vue'
import { normalizeError } from '../../api/errors.js'

const STEPS = [
  { key: 'auth_register', label: '注册' },
  { key: 'auth_login', label: '登录' },
  { key: 'case_create', label: '建案' },
  { key: 'consult_submit', label: '提交咨询' },
  { key: 'review_submit', label: '提交审核' },
]

const days = ref(30)
const loading = ref(false)
const error = ref(null)
const data = ref(null)

const status = computed(() => {
  if (loading.value && !data.value) return 'loading'
  if (error.value) return 'error'
  return 'success'
})

const events = computed(() => data.value?.events || {})
const stepUsers = (key) => events.value[key]?.unique_users || 0
const stepTotal = (key) => events.value[key]?.total || 0
const portalVisits = computed(() => data.value?.portal_visits?.total || 0)
const firstUsers = computed(() => stepUsers('auth_register'))
const overallPct = (key) => (firstUsers.value ? Math.round((stepUsers(key) / firstUsers.value) * 100) : 0)

const funnelRows = computed(() => STEPS.map((step, i) => {
  const users = stepUsers(step.key)
  const prevUsers = i > 0 ? stepUsers(STEPS[i - 1].key) : users
  return {
    label: step.label,
    users,
    total: stepTotal(step.key),
    overallPct: firstUsers.value ? Math.round((users / firstUsers.value) * 100) : 0,
    hopPct: prevUsers ? Math.round((users / prevUsers) * 100) : 0,
    width: firstUsers.value ? Math.round((users / firstUsers.value) * 100) : 0,
    first: i === 0,
  }
}))

const load = async () => {
  loading.value = true
  error.value = null
  try {
    const { data: res } = await api.funnelSummary(days.value)
    data.value = res
  } catch (e) {
    error.value = normalizeError(e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.event-funnel-tab {
  display: grid;
  gap: var(--space-4);
}
/* 漏斗步骤条：步骤 → 去重用户 → 上一跳转化，放 flush body 首行，表格贴卡片边 */
.funnel-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border-light);
  list-style: none;
}
.app-thread.is-static {
  cursor: default;
  border-color: var(--color-border-light);
}
.app-thread.is-static:hover {
  border-color: var(--color-border-light);
  background: var(--color-surface);
}
.app-step-metric {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
}
.app-step-label {
  color: var(--color-text-muted);
}
.app-step-metric .app-num {
  color: var(--color-text);
  font-weight: 600;
}
/* 令牌里的 .app-metric-grid-4 在 1100px 以下直接收成一列，四个读数用两列更合适 */
@media (max-width: 1100px) {
  .funnel-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
