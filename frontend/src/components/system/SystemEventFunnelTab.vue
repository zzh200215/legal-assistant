<template>
  <div class="event-funnel-tab">
    <div class="app-section-intro tab-intro">
      <strong>产品埋点漏斗（服务端事件）</strong>
      <span>注册 → 登录 → 建案 → 提交咨询 → 提交审核，基于服务端埋点事件计数。各阶段为窗口内去重用户数，非同一队列逐步追踪；转化率仅供流失定位参考。</span>
    </div>

    <el-card class="system-panel-card">
      <div class="app-toolbar">
        <span>统计窗口：</span>
        <el-radio-group v-model="days" @change="load">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" style="margin-left: auto" @click="load">刷新</el-button>
      </div>
    </el-card>

    <QueryStateView :status="status" :error="error" min-height="200px" @retry="load">
      <div class="stat-grid">
        <el-card shadow="hover"><el-statistic title="注册用户" :value="stepUsers('auth_register')" /></el-card>
        <el-card shadow="hover"><el-statistic title="提交咨询用户" :value="stepUsers('consult_submit')" /></el-card>
        <el-card shadow="hover"><el-statistic title="注册→咨询转化(%)" :value="overallPct('consult_submit')" /></el-card>
        <el-card shadow="hover"><el-statistic title="门户访问" :value="portalVisits" /></el-card>
      </div>

      <el-card class="system-panel-card">
        <el-table :data="funnelRows" border size="small">
          <el-table-column prop="label" label="阶段" width="150" />
          <el-table-column prop="users" label="去重用户" width="100" />
          <el-table-column prop="total" label="事件量" width="90" />
          <el-table-column label="占注册比例" width="120">
            <template #default="{ row }">{{ row.overallPct }}%</template>
          </el-table-column>
          <el-table-column label="上一跳转化" width="120">
            <template #default="{ row }"><span v-if="row.first">—</span><template v-else>{{ row.hopPct }}%</template></template>
          </el-table-column>
          <el-table-column label="相对占比">
            <template #default="{ row }"><div class="funnel-bar-track"><div class="funnel-bar" :style="{ width: row.width + '%' }"></div></div></template>
          </el-table-column>
        </el-table>
      </el-card>
    </QueryStateView>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/statistic/style/css'
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
.event-funnel-tab { display: grid; gap: var(--space-4); }
.stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.funnel-bar-track { width: 100%; height: 14px; border-radius: 7px; background: var(--color-border); overflow: hidden; }
.funnel-bar { height: 100%; border-radius: 7px; background: var(--color-primary); transition: width 0.3s ease; }
@media (max-width: 760px) { .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
