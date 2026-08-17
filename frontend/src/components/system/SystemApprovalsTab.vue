<template>
  <div class="app-section-intro tab-intro">
    <strong>人工审批中心</strong>
    <span>查看 Agent 高风险工具的审批状态，并快速跳转到执行台完成处理。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <div class="app-empty-note">查看 Agent 高风险工具审批状态，并进入执行台处理待审批请求。</div>
      <el-button :loading="approvalsLoading" @click="fetchApprovalData">刷新</el-button>
    </div>
  </el-card>

  <el-row :gutter="16" style="margin-bottom: 16px">
    <el-col :span="8">
      <el-card shadow="hover">
        <el-statistic title="待审批" :value="approvals.filter((item) => item.status === 'pending').length" />
      </el-card>
    </el-col>
    <el-col :span="8">
      <el-card shadow="hover">
        <el-statistic title="已通过" :value="approvals.filter((item) => item.status === 'approved').length" />
      </el-card>
    </el-col>
    <el-col :span="8">
      <el-card shadow="hover">
        <el-statistic title="已执行" :value="approvals.filter((item) => item.status === 'executed').length" />
      </el-card>
    </el-col>
  </el-row>

  <el-card class="system-panel-card">
    <template #header>审批请求</template>
    <el-table :data="approvals" v-loading="approvalsLoading" border size="small" max-height="520">
      <el-table-column prop="tool_name" label="工具" width="180" />
      <el-table-column prop="agent_type" label="Agent" width="140" />
      <el-table-column prop="risk_level" label="风险级别" width="100">
        <template #default="{ row }">
          <el-tag :type="row.risk_level === 'high' ? 'danger' : 'warning'" size="small">{{ row.risk_level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="110">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : row.status === 'executed' ? 'primary' : 'info'"
          >
            {{ row.status }}
          </el-tag>
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
  </el-card>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemApprovals } from '../../composables/useSystemApprovals'
import { useAuthStore } from '../../stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const { approvalsLoading, approvals, fetchApprovalData } = useSystemApprovals({ client: api, message: ElMessage })

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
</style>
