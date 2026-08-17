<template>
  <div class="app-section-intro tab-intro">
    <strong>平台基础健康</strong>
    <span>查看核心服务可用性、依赖提供方状态和最近一次健康检查时间。</span>
  </div>

  <el-card class="system-panel-card">
    <div class="app-toolbar">
      <div>
        <strong>系统状态：</strong>
        <el-tag :type="healthStatusTagType(healthData.status)" style="margin-left: 8px">
          {{ healthStatusLabel(healthData.status) }}
        </el-tag>
        <span v-if="healthData.timestamp" style="margin-left: 12px; color: var(--color-text-muted)">
          {{ healthData.timestamp }}
        </span>
      </div>
      <el-button :loading="healthLoading" @click="fetchHealthData">刷新</el-button>
    </div>
  </el-card>

  <el-row :gutter="16" class="system-block-row">
    <el-col :span="8" v-for="(check, name) in (healthData.checks || {})" :key="name">
      <el-card shadow="hover">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>{{ healthCheckLabel(name) }}</span>
            <el-tag :type="check.status === 'ok' ? 'success' : 'danger'" size="small">
              {{ check.status === 'ok' ? '正常' : '异常' }}
            </el-tag>
          </div>
        </template>
        <div style="display: grid; gap: 8px; color: var(--color-text-secondary)">
          <div v-if="check.provider"><strong>服务提供方：</strong> {{ check.provider }}</div>
          <div v-if="check.message" style="color: var(--color-danger); white-space: pre-wrap">{{ check.message }}</div>
          <div v-if="!check.message" style="color: var(--color-success)">连接正常</div>
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemObservability } from '../../composables/useSystemObservability'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { healthLoading, healthData, fetchHealthData } = useSystemObservability({ client: api, message: ElMessage, isAdmin })

const healthCheckLabel = (name) => ({
  database: '数据库',
  redis: 'Redis',
  llm_provider: '模型服务',
  ollama: 'Ollama',
}[name] || name)

const healthStatusLabel = (status) => ({
  ok: '正常',
  degraded: '降级',
  error: '异常',
}[status] || '未知')

const healthStatusTagType = (status) => ({
  ok: 'success',
  degraded: 'warning',
  error: 'danger',
}[status] || 'info')

onMounted(async () => {
  await authStore.loadMe()
  fetchHealthData()
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
