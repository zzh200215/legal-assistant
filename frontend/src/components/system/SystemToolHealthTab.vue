<template>
  <div class="app-section-intro tab-intro">
    <strong>工具健康概览</strong>
    <span>跟踪 MCP 工具调用量、成功率、审批挂起次数和最近错误。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <div class="app-empty-note">查看 MCP 工具最近调用量、成功率、审批挂起次数与最近错误。</div>
      <el-button :loading="toolHealthLoading" @click="fetchToolHealth">刷新</el-button>
    </div>
  </el-card>

  <el-card class="system-panel-card">
    <template #header>工具健康概览</template>
    <el-table :data="toolHealthRows" v-loading="toolHealthLoading" border size="small" max-height="560">
      <el-table-column prop="tool_name" label="工具" width="180" />
      <el-table-column prop="calls" label="调用数" width="90" />
      <el-table-column prop="success_rate" label="成功率" width="100">
        <template #default="{ row }">{{ formatRate(row.success_rate) }}</template>
      </el-table-column>
      <el-table-column prop="failed_calls" label="失败数" width="90" />
      <el-table-column prop="pending_approval_calls" label="审批挂起" width="100" />
      <el-table-column prop="avg_duration_ms" label="平均耗时(ms)" width="120" />
      <el-table-column prop="last_error" label="最近错误" show-overflow-tooltip />
      <el-table-column prop="last_called_at" label="最近调用" width="180" />
    </el-table>
  </el-card>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemObservability } from '../../composables/useSystemObservability'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { toolHealthLoading, toolHealthRows, fetchToolHealth } = useSystemObservability({ client: api, message: ElMessage, isAdmin })

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
</style>
