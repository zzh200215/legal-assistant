<template>
  <div class="app-section-intro tab-intro">
    <strong>异常与风险告警</strong>
    <span>聚合审查、法源与外发风险，按来源、分类和级别快速筛查。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <span>统计周期：</span>
      <el-radio-group v-model="alertDays" @change="resetAlertPagination">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <template v-if="isAdmin">
        <span style="margin-left: 12px">范围：</span>
        <el-radio-group v-model="alertScope" @change="resetAlertPagination">
          <el-radio-button value="mine">我的</el-radio-button>
          <el-radio-button value="all">全部用户</el-radio-button>
        </el-radio-group>
      </template>
      <span style="margin-left: 12px">来源：</span>
      <el-select v-model="alertSource" clearable placeholder="全部" style="width: 140px" @change="resetAlertPagination">
        <el-option label="异步任务" value="async_task" />
        <el-option label="Agent" value="agent" />
        <el-option label="计划任务" value="scheduler" />
        <el-option label="法源同步" value="mailbox" />
        <el-option label="外发文书" value="outbound_email" />
      </el-select>
      <span>分类：</span>
      <el-select v-model="alertCategory" clearable placeholder="全部" style="width: 180px" @change="resetAlertPagination">
        <el-option v-for="item in alertCategoryOptions" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <span>级别：</span>
      <el-select v-model="alertSeverity" clearable placeholder="全部" style="width: 140px" @change="resetAlertPagination">
        <el-option label="高" value="high" />
        <el-option label="中" value="medium" />
        <el-option label="低" value="low" />
      </el-select>
      <div class="app-toolbar-right">
        <el-button @click="fetchAlerts">刷新</el-button>
      </div>
    </div>
  </el-card>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="告警总数" :value="alertStats.total || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="计划任务风险" :value="alertStats.by_source?.scheduler || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="法源同步失败" :value="alertStats.by_source?.mailbox || 0" />
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card shadow="hover">
        <el-statistic title="外发文书风险" :value="alertStats.by_source?.outbound_email || 0" />
      </el-card>
    </el-col>
  </el-row>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="12">
      <el-card>
        <template #header>分类分布</template>
        <el-table :data="alertCategoryRows" border size="small" max-height="260">
          <el-table-column prop="label" label="分类" />
          <el-table-column prop="count" label="数量" width="100" />
        </el-table>
      </el-card>
    </el-col>
    <el-col :span="12">
      <el-card>
        <template #header>每日告警趋势</template>
        <el-table :data="alertDateRows" border size="small" max-height="260">
          <el-table-column prop="date" label="日期" width="140" />
          <el-table-column prop="count" label="告警数" width="100" />
        </el-table>
      </el-card>
    </el-col>
  </el-row>

  <el-card class="system-panel-card">
    <template #header>告警列表</template>
    <el-table :data="alerts" v-loading="alertLoading" border size="small" max-height="520">
      <el-table-column prop="source" label="来源" width="110">
        <template #default="{ row }">
          <el-tag :type="alertSourceTagType(row.source)" size="small">
            {{ row.source_label || row.source }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="severity" label="级别" width="90">
        <template #default="{ row }">
          <el-tag :type="alertSeverityTagType(row.severity)" size="small">
            {{ alertSeverityLabel(row.severity) }}
          </el-tag>
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
      <el-table-column prop="created_at" label="时间" width="180" />
    </el-table>
    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="alertPage"
      :page-size="alertPageSize"
      :total="alertTotal"
      class="app-pagination-end"
      @current-change="handleAlertPageChange"
    />
  </el-card>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemActivity } from '../../composables/useSystemActivity'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  alertDays, alertScope, alertSource, alertCategory, alertSeverity, alerts, alertStats,
  alertLoading, alertPage, alertPageSize, alertTotal, fetchAlerts,
  resetAlertPagination, handleAlertPageChange,
} = useSystemActivity({ client: api, message: ElMessage, isAdmin })

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

const alertSeverityTagType = (severity) => ({
  high: 'danger',
  medium: 'warning',
  low: 'info',
}[severity] || 'info')

const alertSourceTagType = (source) => ({
  agent: 'danger',
  async_task: 'warning',
  scheduler: 'danger',
  mailbox: 'warning',
  outbound_email: 'warning',
}[source] || 'info')

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
.system-block-row {
  margin-top: var(--space-4);
  margin-bottom: 0;
}
</style>
