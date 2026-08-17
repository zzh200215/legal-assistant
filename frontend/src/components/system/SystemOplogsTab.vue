<template>
  <div class="app-section-intro tab-intro">
    <strong>操作回放</strong>
    <span>按模块、范围和时间回看系统行为，定位用户操作和平台侧事件。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <span>统计周期：</span>
      <el-radio-group v-model="logDays" @change="resetLogPagination">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <template v-if="isAdmin">
        <span style="margin-left: 12px">范围：</span>
        <el-radio-group v-model="logScope" @change="resetLogPagination">
          <el-radio-button value="mine">我的</el-radio-button>
          <el-radio-button value="all">全部用户</el-radio-button>
        </el-radio-group>
      </template>
      <span style="margin-left: 12px">模块筛选：</span>
      <el-select v-model="logModule" clearable placeholder="全部" style="width: 140px" @change="resetLogPagination">
        <el-option label="文档" value="document" />
        <el-option label="法律文书" value="legal" />
        <el-option label="任务" value="task" />
        <el-option label="Agent" value="agent" />
        <el-option label="异步任务" value="async_task" />
        <el-option v-if="isAdmin" label="系统" value="system" />
        <el-option label="聊天" value="chat" />
        <el-option label="提示词" value="prompt" />
        <el-option label="认证" value="auth" />
      </el-select>
      <div class="app-toolbar-right">
        <el-button @click="fetchLogData">刷新</el-button>
      </div>
    </div>
  </el-card>

  <el-card v-if="logStats.total_operations" class="system-panel-card">
    <template #header>操作统计</template>
    <el-descriptions :column="4" border>
      <el-descriptions-item label="总操作数">{{ logStats.total_operations }}</el-descriptions-item>
      <el-descriptions-item v-for="(count, mod) in logStats.by_module" :key="mod" :label="moduleLabel(mod)">
        {{ count }}
      </el-descriptions-item>
    </el-descriptions>
  </el-card>

  <el-card class="system-panel-card">
    <template #header>操作记录</template>
    <el-table :data="logs" v-loading="logLoading" border size="small" max-height="500">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="module" label="模块" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="moduleTagType(row.module)">{{ moduleLabel(row.module) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="action" label="操作" show-overflow-tooltip />
      <el-table-column prop="target_type" label="目标类型" width="100" />
      <el-table-column prop="target_id" label="目标 ID" width="80" />
      <el-table-column prop="detail" label="详情" show-overflow-tooltip />
      <el-table-column prop="ip_address" label="IP" width="130" />
      <el-table-column prop="created_at" label="时间" width="170" />
    </el-table>
    <el-pagination
      background
      layout="total, prev, pager, next"
      :current-page="logPage"
      :page-size="logPageSize"
      :total="logTotal"
      class="app-pagination-end"
      @current-change="handleLogPageChange"
    />
  </el-card>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemActivity } from '../../composables/useSystemActivity'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  logDays, logModule, logScope, logs, logStats, logLoading, logPage, logPageSize, logTotal,
  fetchLogData, resetLogPagination, handleLogPageChange,
} = useSystemActivity({ client: api, message: ElMessage, isAdmin })

const moduleLabel = (mod) => ({
  document: '文档',
  legal: '法律文书',
  task: '任务',
  agent: 'Agent',
  async_task: '异步任务',
  system: '系统',
  chat: '聊天',
  prompt: '提示词',
  auth: '认证',
}[mod] || mod)

const moduleTagType = (mod) => ({
  document: '',
  legal: 'warning',
  task: 'info',
  agent: 'danger',
  async_task: 'warning',
  system: 'info',
  chat: '',
  prompt: 'success',
  auth: 'warning',
}[mod] || '')

onMounted(async () => {
  await authStore.loadMe()
  fetchLogData()
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
