<template>
  <div class="app-section-intro tab-intro">
    <strong>人工审批中心</strong>
    <span>查看 Agent 高风险工具的审批状态，并快速跳转到执行台完成处理。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-empty-note">查看 Agent 高风险工具审批状态，并进入执行台处理待审批请求。</span>
        <div class="app-filter-bar-actions">
          <el-button :loading="approvalsLoading" @click="fetchApprovalData">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-3 approvals-metric-grid">
    <div class="app-stat">
      <span class="app-stat-label">待审批</span>
      <span class="app-stat-value">{{ approvalStats.pending }}<small>项</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">已通过</span>
      <span class="app-stat-value">{{ approvalStats.approved }}<small>项</small></span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">已执行</span>
      <span class="app-stat-value">{{ approvalStats.executed }}<small>项</small></span>
    </div>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>审批请求</h2>
      <span class="app-card-head-meta">共 {{ approvals.length }} 条</span>
    </div>
    <!-- 有数据时表头贴卡片头；空/加载/错误态退回常规内边距，避免状态块贴卡片边 -->
    <div class="app-card-body" :class="{ 'is-flush': approvals.length }">
      <QueryStateView :status="approvalsStatus" :error="approvalsError" empty-text="暂无待审批项" min-height="200px" @retry="fetchApprovalData">
      <div class="app-table-wrap">
        <el-table v-loading="approvalsLoading" :data="approvals" border size="small" max-height="520">
          <el-table-column prop="tool_name" label="工具" width="180" />
          <el-table-column prop="agent_type" label="Agent" width="140" />
          <el-table-column prop="risk_level" label="风险级别" width="100">
            <template #default="{ row }">
              <span class="app-tag" :class="riskTagClass(row.risk_level)">{{ row.risk_level }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <span class="app-tag" :class="approvalStatusTagClass(row.status)">{{ row.status }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="input_params" label="参数" show-overflow-tooltip />
          <el-table-column prop="created_at" label="创建时间" width="180" />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.status === 'pending'" text type="primary" @click="openApprovalInAgent(row)">去处理</el-button>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
      </QueryStateView>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemApprovals } from '../../composables/useSystemApprovals'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const router = useRouter()
const authStore = useAuthStore()

const { approvalsLoading, approvalsError, approvals, approvalStats, fetchApprovalData } = useSystemApprovals({ client: api })

const approvalsStatus = computed(() => {
  if (approvalsLoading.value && !approvals.value.length) return 'loading'
  if (approvalsError.value) return 'error'
  if (!approvals.value.length) return 'empty'
  return 'success'
})

// 状态 / 风险 → .app-tag 语义变体（方向 B 的标签只用浅底深字，不用 Element type 色板）
const riskTagClass = (level) => (level === 'high' ? 'is-danger' : 'is-pending')
const approvalStatusTagClass = (status) => ({
  pending: 'is-pending', approved: 'is-ok', executed: 'is-info', rejected: 'is-danger',
}[status] || '')

const openApprovalInAgent = (row) => {
  router.push({ path: '/agent', query: { tab: 'approvals', approvalId: String(row.id) } })
}

onMounted(async () => {
  await authStore.loadMe()
  fetchApprovalData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.approvals-metric-grid {
  margin-top: var(--space-4);
}
</style>
