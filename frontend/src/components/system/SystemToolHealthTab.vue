<template>
  <div class="app-section-intro tab-intro">
    <strong>工具健康概览</strong>
    <span>跟踪 MCP 工具调用量、成功率、审批挂起次数和最近错误。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <div class="app-empty-note">查看 MCP 工具最近调用量、成功率、审批挂起次数与最近错误。</div>
        <div class="app-filter-bar-actions">
          <el-button :loading="toolHealthLoading" @click="fetchToolHealth">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-4 tool-health-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">工具数</span>
      <span class="app-stat-value">{{ toolHealthRows.length }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">调用数</span>
      <span class="app-stat-value">{{ toolHealthTotals.calls }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">失败数</span>
      <span class="app-stat-value">{{ toolHealthTotals.failed }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">审批挂起</span>
      <span class="app-stat-value">{{ toolHealthTotals.pending }}</span>
    </div>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>工具健康概览</h2>
      <span class="app-card-head-meta">共 {{ toolHealthRows.length }} 个工具</span>
    </div>
    <div class="app-card-body">
      <div class="app-table-wrap">
        <QueryStateView :status="toolHealthStatus" :error="toolHealthError" empty-text="暂无工具调用数据" min-height="200px" @retry="fetchToolHealth">
        <el-table v-loading="toolHealthLoading" :data="toolHealthRows" border size="small" max-height="560">
          <el-table-column prop="tool_name" label="工具" width="180" />
          <el-table-column prop="calls" label="调用数" width="90">
            <template #default="{ row }"><span class="app-num">{{ row.calls }}</span></template>
          </el-table-column>
          <el-table-column prop="success_rate" label="成功率" width="100">
            <template #default="{ row }"><span class="app-num">{{ formatRate(row.success_rate) }}</span></template>
          </el-table-column>
          <el-table-column prop="failed_calls" label="失败数" width="90">
            <template #default="{ row }"><span class="app-num">{{ row.failed_calls }}</span></template>
          </el-table-column>
          <el-table-column prop="pending_approval_calls" label="审批挂起" width="100">
            <template #default="{ row }"><span class="app-num">{{ row.pending_approval_calls }}</span></template>
          </el-table-column>
          <el-table-column prop="avg_duration_ms" label="平均耗时(ms)" width="120">
            <template #default="{ row }"><span class="app-num">{{ row.avg_duration_ms }}</span></template>
          </el-table-column>
          <el-table-column prop="last_error" label="最近错误" show-overflow-tooltip />
          <el-table-column prop="last_called_at" label="最近调用" width="180">
            <template #default="{ row }"><span class="app-num">{{ row.last_called_at }}</span></template>
          </el-table-column>
        </el-table>
        </QueryStateView>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemObservability } from '../../composables/useSystemObservability'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { toolHealthLoading, toolHealthRows, toolHealthError, fetchToolHealth } = useSystemObservability({ client: api, message: ElMessage, isAdmin })
// 三态（ux-audit M-13）：保留 v-loading 的刷新遮罩，叠加 QueryStateView 的空态与失败重试
const toolHealthStatus = computed(() => {
  if (toolHealthLoading.value && !toolHealthRows.value.length) return 'loading'
  if (toolHealthError.value) return 'error'
  if (!toolHealthRows.value.length) return 'empty'
  return 'success'
})

// 顶部指标从已有行数据里算，不新增请求
const toolHealthTotals = computed(() => toolHealthRows.value.reduce((acc, row) => {
  acc.calls += Number(row.calls) || 0
  acc.failed += Number(row.failed_calls) || 0
  acc.pending += Number(row.pending_approval_calls) || 0
  return acc
}, { calls: 0, failed: 0, pending: 0 }))

const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value) * 100)}%`
}

onMounted(async () => {
  await authStore.loadMe()
  fetchToolHealth()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.tool-health-metric-grid {
  margin-top: var(--space-4);
}
</style>
