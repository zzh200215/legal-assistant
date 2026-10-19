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

  <!-- 检索索引：BM25 内存索引的运行计数，用来确认「偶发回退全表扫描」是否还在发生 -->
  <section class="app-card health-index-card">
    <div class="app-card-head">
      <h3>检索索引</h3>
      <span class="app-card-head-meta">内存 BM25 关键词索引（进程内共享，懒构建 + TTL 兜底重建）</span>
      <el-button class="health-refresh" :loading="indexLoading" @click="fetchIndexStats">刷新</el-button>
    </div>
    <div class="app-card-body">
      <div class="app-metric-grid-4 health-index-grid">
        <div class="app-stat">
          <span class="app-stat-label">索引状态</span>
          <strong class="app-stat-value">{{ indexStats.index_ready ? '就绪' : '未构建' }}</strong>
          <span class="app-stat-note">{{ indexStats.enabled ? `TTL ${indexStats.ttl_seconds}s` : '已关闭（走 jieba 扫描）' }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">索引条目</span>
          <strong class="app-stat-value">{{ indexStats.index_size }}</strong>
          <span class="app-stat-note">{{ indexStats.built_at ? `构建于 ${formatStamp(indexStats.built_at)}` : '尚未构建' }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">构建次数</span>
          <strong class="app-stat-value">{{ indexStats.builds }}</strong>
          <span class="app-stat-note">{{ buildNote }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">回退扫描</span>
          <strong class="app-stat-value">{{ indexStats.fallback_scans }}</strong>
          <span class="app-stat-note">索引不可用时的 jieba 全表扫描次数</span>
        </div>
      </div>
      <p class="health-index-note">{{ indexNote }}</p>
    </div>
  </section>
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

const { healthLoading, healthData, fetchHealthData, indexLoading, indexStats, indexError, fetchIndexStats } = useSystemObservability({ client: api, message: ElMessage, isAdmin })

// 构建口径：失败与陈旧丢弃都如实报出来（它们是「索引为什么没就绪」的直接解释）
const buildNote = computed(() => {
  const parts = []
  if (indexStats.value.last_build_ms !== null && indexStats.value.last_build_ms !== undefined) {
    parts.push(`最近 ${indexStats.value.last_build_ms}ms`)
  }
  if (indexStats.value.build_failures) parts.push(`失败 ${indexStats.value.build_failures}`)
  if (indexStats.value.stale_discarded) parts.push(`陈旧丢弃 ${indexStats.value.stale_discarded}`)
  if (indexStats.value.ttl_rebuilds) parts.push(`TTL 重建 ${indexStats.value.ttl_rebuilds}`)
  return parts.length ? parts.join(' · ') : '暂无构建记录'
})

const indexNote = computed(() => {
  if (indexError.value) return `读取失败：${indexError.value}`
  if (!indexStats.value.enabled) return 'BM25 已关闭，关键词召回每次都走 jieba 全表扫描（功能正确，只是慢）。'
  if (indexStats.value.fallback_scans === 0) return '本次进程内没有发生回退扫描。'
  return `已发生 ${indexStats.value.fallback_scans} 次回退扫描；若同时有构建失败，说明索引没能建立，检索退化为全表扫描。`
})

const formatStamp = (value) => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false })
}

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
  fetchIndexStats()
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
.health-index-card {
  margin-top: var(--space-4);
}
.health-index-grid {
  margin-top: 0;
}
.app-stat-note {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
.health-index-note {
  margin: var(--space-3) 0 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.7;
}
</style>
