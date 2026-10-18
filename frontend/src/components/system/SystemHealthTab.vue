<template>
  <div class="app-section-intro tab-intro">
    <strong>平台基础健康</strong>
    <span>查看核心服务可用性、依赖提供方状态和最近一次健康检查时间。</span>
  </div>

  <!-- 状态条不再套卡片：一行标签 + 刷新，直接贴在内容区顶部 -->
  <div class="health-status-bar">
    <strong>系统状态</strong>
    <span class="app-tag" :class="healthStatusTone(healthData.status)">{{ healthStatusLabel(healthData.status) }}</span>
    <span v-if="healthData.timestamp" class="health-status-time">{{ healthData.timestamp }}</span>
    <el-button class="health-refresh" :loading="healthLoading" @click="fetchHealthData">刷新</el-button>
  </div>

  <div class="app-panel-grid-3 health-check-grid">
    <section v-for="(check, name) in (healthData.checks || {})" :key="name" class="app-card">
      <div class="app-card-head">
        <h3>{{ healthCheckLabel(name) }}</h3>
        <span class="app-tag" :class="check.status === 'ok' ? 'is-ok' : 'is-danger'">
          {{ check.status === 'ok' ? '正常' : '异常' }}
        </span>
      </div>
      <div class="app-card-body health-check-body">
        <div v-if="check.provider"><strong>服务提供方：</strong> {{ check.provider }}</div>
        <div v-if="check.message" class="health-check-error">{{ check.message }}</div>
        <div v-else class="health-check-ok">连接正常</div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/button/style/css'
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

// 方向 B 的标签口径：正常 / 待确认（降级）/ 异常
const healthStatusTone = (status) => ({
  ok: 'is-ok',
  degraded: 'is-pending',
  error: 'is-danger',
}[status] || '')

onMounted(async () => {
  await authStore.loadMe()
  fetchHealthData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.health-status-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-top: var(--space-4);
  color: var(--color-text);
  font-size: var(--text-sm);
}
.health-status-time {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.health-refresh {
  margin-left: auto;
}
.health-check-grid {
  margin-top: var(--space-4);
}
.health-check-body {
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.health-check-error {
  color: var(--color-danger);
  white-space: pre-wrap;
}
.health-check-ok {
  color: var(--color-success);
}
</style>
