<template>
  <div class="app-section-intro tab-intro">
    <strong>异常与风险告警</strong>
    <span>聚合审查、法源与外发风险，按来源、分类和级别快速筛查。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-filter-label">统计周期：</span>
        <el-radio-group v-model="alertDays" @change="resetAlertPagination">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <template v-if="isAdmin">
          <span class="app-filter-label">范围：</span>
          <el-radio-group v-model="alertScope" @change="resetAlertPagination">
            <el-radio-button value="mine">我的</el-radio-button>
            <el-radio-button value="all">全部用户</el-radio-button>
          </el-radio-group>
        </template>
        <span class="app-filter-label">来源：</span>
        <el-select v-model="alertSource" clearable placeholder="全部" class="alerts-source-select" @change="resetAlertPagination">
          <el-option label="异步任务" value="async_task" />
          <el-option label="Agent" value="agent" />
          <el-option label="计划任务" value="scheduler" />
          <el-option label="法源同步" value="mailbox" />
          <el-option label="外发文书" value="outbound_email" />
        </el-select>
        <span class="app-filter-label">分类：</span>
        <el-select v-model="alertCategory" clearable placeholder="全部" class="alerts-category-select" @change="resetAlertPagination">
          <el-option v-for="item in alertCategoryOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <span class="app-filter-label">级别：</span>
        <el-select v-model="alertSeverity" clearable placeholder="全部" class="alerts-severity-select" @change="resetAlertPagination">
          <el-option label="高" value="high" />
          <el-option label="中" value="medium" />
          <el-option label="低" value="low" />
        </el-select>
        <div class="app-filter-bar-actions">
          <el-button @click="fetchAlerts">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-4 alerts-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">告警总数</span>
      <span class="app-stat-value">{{ alertStats.total || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">计划任务风险</span>
      <span class="app-stat-value">{{ alertStats.by_source?.scheduler || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">法源同步失败</span>
      <span class="app-stat-value">{{ alertStats.by_source?.mailbox || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">外发文书风险</span>
      <span class="app-stat-value">{{ alertStats.by_source?.outbound_email || 0 }}</span>
    </div>
  </div>

  <div class="app-panel-grid-2 alerts-breakdown-grid">
    <section class="app-card">
      <div class="app-card-head">
        <h2>分类分布</h2>
        <span class="app-card-head-meta">共 {{ alertCategoryRows.length }} 项</span>
      </div>
      <div class="app-card-body">
        <div class="app-table-wrap">
          <el-table :data="alertCategoryRows" border size="small" max-height="260">
            <el-table-column prop="label" label="分类" />
            <el-table-column prop="count" label="数量" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.count }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
    <section class="app-card">
      <div class="app-card-head">
        <h2>每日告警趋势</h2>
        <span class="app-card-head-meta">近 {{ alertDays }} 天</span>
      </div>
      <div class="app-card-body">
        <div class="app-table-wrap">
          <el-table :data="alertDateRows" border size="small" max-height="260">
            <el-table-column prop="date" label="日期" width="140">
              <template #default="{ row }"><span class="app-num">{{ row.date }}</span></template>
            </el-table-column>
            <el-table-column prop="count" label="告警数" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.count }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>告警列表</h2>
      <span class="app-card-head-meta">共 {{ alertTotal }} 条</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="alertStatus" :error="alertError" empty-text="暂无告警" min-height="200px" @retry="fetchAlerts">
        <el-table v-loading="alertLoading" :data="alerts" border size="small" max-height="520">
          <el-table-column prop="source" label="来源" width="110">
            <template #default="{ row }">
              <span class="app-tag" :class="alertSourceTagClass(row.source)">
                {{ row.source_label || row.source }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="severity" label="级别" width="90">
            <template #default="{ row }">
              <span class="app-tag" :class="alertSeverityTagClass(row.severity)">
                {{ alertSeverityLabel(row.severity) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="分类" width="150">
            <template #default="{ row }">
              {{ alertCategoryLabel(row.category) }}
            </template>
          </el-table-column>
          <el-table-column prop="title" label="事件" width="220" show-overflow-tooltip />
          <el-table-column prop="error_type" label="错误类型" width="160" show-overflow-tooltip />
          <el-table-column prop="target_type" label="目标类型" width="100" />
          <el-table-column prop="target_id" label="目标 ID" width="90" />
          <el-table-column prop="message" label="详情" show-overflow-tooltip />
          <el-table-column prop="created_at" label="时间" width="180">
            <template #default="{ row }"><span class="app-num">{{ row.created_at }}</span></template>
          </el-table-column>
        </el-table>
        </QueryStateView>
      </div>
      <el-pagination
        background
        layout="total, prev, pager, next"
        :current-page="alertPage"
        :page-size="alertPageSize"
        :total="alertTotal"
        class="app-pagination-end"
        @current-change="handleAlertPageChange"
      />
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemActivity } from '../../composables/useSystemActivity'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  alertDays, alertScope, alertSource, alertCategory, alertSeverity, alerts, alertStats,
  alertLoading, alertError, alertPage, alertPageSize, alertTotal, fetchAlerts,
  resetAlertPagination, handleAlertPageChange,
} = useSystemActivity({ client: api, message: ElMessage, isAdmin })

const alertStatus = computed(() => {
  if (alertLoading.value && !alerts.value.length) return 'loading'
  if (alertError.value) return 'error'
  if (!alerts.value.length) return 'empty'
  return 'success'
})

const alertCategoryOptions = [
  { label: '模型错误', value: 'model_error' },
  { label: '工具错误', value: 'tool_error' },
  { label: '超时错误', value: 'timeout_error' },
  { label: '权限错误', value: 'permission_error' },
  { label: '数据错误', value: 'data_error' },
  { label: '网络错误', value: 'network_error' },
  { label: '异步任务错误', value: 'async_task_error' },
  { label: 'Agent 错误', value: 'agent_error' },
  { label: '计划任务错误', value: 'scheduler_error' },
  { label: '计划任务延迟', value: 'scheduler_delay' },
  { label: '法源同步错误', value: 'mailbox_sync_error' },
  { label: 'SMTP 外发错误', value: 'outbound_email_error' },
  { label: '外发待审批', value: 'approval_pending' },
  { label: '系统错误', value: 'system_error' },
]

const alertCategoryRows = computed(() => {
  const byCategory = alertStats.value.by_category || {}
  return Object.entries(byCategory)
    .map(([key, count]) => ({ key, label: alertCategoryLabel(key), count }))
    .sort((a, b) => b.count - a.count)
})

const alertDateRows = computed(() => {
  const byDate = alertStats.value.by_date || {}
  return Object.entries(byDate)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const alertCategoryLabel = (category) => ({
  model_error: '模型错误',
  tool_error: '工具错误',
  timeout_error: '超时错误',
  permission_error: '权限错误',
  data_error: '数据错误',
  network_error: '网络错误',
  async_task_error: '异步任务错误',
  agent_error: 'Agent 错误',
  scheduler_error: '计划任务错误',
  scheduler_delay: '计划任务延迟',
  mailbox_sync_error: '法源同步错误',
  outbound_email_error: 'SMTP 外发错误',
  approval_pending: '外发待审批',
  system_error: '系统错误',
}[category] || category || '未分类')

const alertSeverityLabel = (severity) => ({
  high: '高',
  medium: '中',
  low: '低',
}[severity] || severity || '未知')

// 方向 B：语义色只从 .app-tag 变体里选（is-danger / is-pending / is-info）
const alertSeverityTagClass = (severity) => ({
  high: 'is-danger',
  medium: 'is-pending',
  low: 'is-info',
}[severity] || 'is-info')

const alertSourceTagClass = (source) => ({
  agent: 'is-danger',
  async_task: 'is-pending',
  scheduler: 'is-danger',
  mailbox: 'is-pending',
  outbound_email: 'is-pending',
}[source] || 'is-info')

onMounted(async () => {
  await authStore.loadMe()
  fetchAlerts()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.alerts-metric-grid,
.alerts-breakdown-grid {
  margin-top: var(--space-4);
}
/* 筛选下拉沿用原来的宽度（内联 style 收进 scoped 类） */
.alerts-source-select {
  width: 140px;
}
.alerts-category-select {
  width: 180px;
}
.alerts-severity-select {
  width: 140px;
}
</style>
