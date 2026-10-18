<template>
  <!-- 方向 B：数据块走 .app-stat / .app-metric-grid-4，状态走 .app-tag 变体 -->
  <div class="app-metric-grid-4 system-overview">
    <div class="app-stat">
      <span class="app-stat-label">运行任务</span>
      <strong class="app-stat-value">{{ runningTaskCount }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">失败任务</span>
      <strong class="app-stat-value">{{ failedTaskCount }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">待重试</span>
      <strong class="app-stat-value">{{ retryableTaskCount }}</strong>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">Agent 成功率</span>
      <strong class="app-stat-value">{{ Math.round(agentSuccessRate * 100) }}<small>%</small></strong>
    </div>
  </div>

  <div class="system-command-bar">
    <span class="app-tag">当前标签：{{ activeTabLabel }}</span>
    <span class="app-tag" :class="failedTaskCount ? 'is-danger' : 'is-ok'">失败任务：{{ failedTaskCount }}</span>
    <span class="app-tag" :class="pendingApprovalCount ? 'is-pending' : 'is-ok'">待审批：{{ pendingApprovalCount }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import api from '../../api'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useSystemApprovals } from '../../composables/useSystemApprovals'
import { useAuthStore } from '../../stores/auth'

const props = defineProps({
  activeTab: { type: String, default: 'health' },
})

// 与 System.vue 的 tab 名一一对应；状态条上显示中文标签而不是内部 key
const TAB_LABELS = {
  health: '健康检查',
  funnel: '用户漏斗',
  event_funnel: '事件漏斗',
  retention: '留存与北极星',
  tokens: 'Token 统计',
  oplogs: '操作日志',
  alerts: '异常告警',
  experiments: '实验观测',
  feedback: '反馈闭环',
  tool_health: '工具健康',
  approvals: '审批台',
  knowledge: '知识库',
  orgs: '组织架构',
  sensitivity: '敏感治理',
  tasks: '任务中心',
}

const activeTabLabel = computed(() => TAB_LABELS[props.activeTab] || props.activeTab)

const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { runningTaskCount, failedTaskCount, retryableTaskCount, agentSuccessRate } = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin })
const { approvalStats } = useSystemApprovals({ client: api, message: ElMessage })
const pendingApprovalCount = computed(() => approvalStats.value?.pending || 0)
</script>

<style scoped>
/* 状态条不再套卡片：只留一行标签，避免和下方 tab 内容形成卡中卡 */
.system-command-bar {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

@media (max-width: 1100px) {
  /* 令牌里的 .app-metric-grid-4 在 1100px 以下直接收成一列，四个读数用两列更合适 */
  .system-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
