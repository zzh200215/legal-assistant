<template>
  <div class="system-page">
    <!-- 方向 B 页头：标题与副标题走 .app-page-head 骨架，页面内不再包一层卡片 -->
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">系统</h1>
        <p class="app-page-sub">健康检查、组织架构、任务运行与治理观测的统一入口。</p>
      </div>
      <div class="app-page-head-actions">
        <span class="app-tag is-info">仅管理员可见</span>
      </div>
    </header>

    <SystemOverviewBar :active-tab="activeTab" />

    <el-tabs v-model="activeTab" class="system-tabs" @tab-change="syncTabQuery">
      <el-tab-pane label="健康检查" name="health">
        <SystemHealthTab />
      </el-tab-pane>
      <!-- lazy：tab 首次激活才挂载加载，避免进入系统中心时 14 个 tab 并发打满后端 -->
      <el-tab-pane label="用户漏斗" name="funnel" lazy>
        <PilotAnalyticsTabs section="funnel" />
      </el-tab-pane>
      <el-tab-pane label="事件漏斗" name="event_funnel" lazy>
        <SystemEventFunnelTab />
      </el-tab-pane>
      <el-tab-pane label="留存与北极星" name="retention" lazy>
        <PilotAnalyticsTabs section="retention" />
      </el-tab-pane>
      <el-tab-pane label="Token 统计" name="tokens" lazy>
        <SystemTokensTab />
      </el-tab-pane>
      <el-tab-pane label="操作日志" name="oplogs" lazy>
        <SystemOplogsTab />
      </el-tab-pane>
      <el-tab-pane label="异常告警" name="alerts" lazy>
        <SystemAlertsTab />
      </el-tab-pane>
      <el-tab-pane label="实验观测" name="experiments" lazy>
        <SystemExperimentsTab />
      </el-tab-pane>
      <el-tab-pane label="反馈闭环" name="feedback" lazy>
        <SystemFeedbackTab />
      </el-tab-pane>
      <el-tab-pane label="工具健康" name="tool_health" lazy>
        <SystemToolHealthTab />
      </el-tab-pane>
      <el-tab-pane label="审批台" name="approvals" lazy>
        <SystemApprovalsTab />
      </el-tab-pane>
      <el-tab-pane label="知识库" name="knowledge" lazy>
        <SystemKnowledgeTab :open-document="openKnowledgeDocument" />
      </el-tab-pane>
      <el-tab-pane label="组织架构" name="orgs" lazy>
        <SystemOrgTab />
      </el-tab-pane>
      <el-tab-pane label="敏感治理" name="sensitivity" lazy>
        <SystemSensitivityTab :open-document="openKnowledgeDocument" />
      </el-tab-pane>
      <el-tab-pane label="任务中心" name="tasks" lazy>
        <SystemTasksTab />
      </el-tab-pane>
      <el-tab-pane label="投递看板" name="delivery" lazy>
        <SystemDeliveryTab />
      </el-tab-pane>
      <el-tab-pane label="通知模板" name="templates" lazy>
        <SystemNotificationTemplatesTab />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { defineAsyncComponent, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/tabs/style/css'
import SystemOverviewBar from '../components/system/SystemOverviewBar.vue'
import SystemHealthTab from '../components/system/SystemHealthTab.vue'
import SystemTasksTab from '../components/system/SystemTasksTab.vue'
import SystemOplogsTab from '../components/system/SystemOplogsTab.vue'
import SystemAlertsTab from '../components/system/SystemAlertsTab.vue'
import SystemExperimentsTab from '../components/system/SystemExperimentsTab.vue'
import SystemFeedbackTab from '../components/system/SystemFeedbackTab.vue'
import SystemToolHealthTab from '../components/system/SystemToolHealthTab.vue'
import SystemApprovalsTab from '../components/system/SystemApprovalsTab.vue'
import SystemKnowledgeTab from '../components/system/SystemKnowledgeTab.vue'
import SystemOrgTab from '../components/system/SystemOrgTab.vue'
import SystemSensitivityTab from '../components/system/SystemSensitivityTab.vue'
import PilotAnalyticsTabs from '../components/system/PilotAnalyticsTabs.vue'
import SystemEventFunnelTab from '../components/system/SystemEventFunnelTab.vue'

// tab 组件按需加载：tab 本身已经是 lazy（首次激活才渲染），但静态 import 会把全部组件打进
// System 路由 chunk（15 个 tab 一度把页面 chunk 顶到 130 KB 预算线）。只把体量最大的
// Token 统计（22.5 KB）拆成异步 chunk——既压住页面 chunk，又不让整体资产因过多小 chunk 膨胀
// （每拆一个 chunk 都要多付一份 chunk 头与独立 CSS，总资产预算只剩几 KB 余量）。
const asyncTab = (loader) => defineAsyncComponent({ loader, delay: 0 })
const SystemTokensTab = asyncTab(() => import('../components/system/SystemTokensTab.vue'))
// 投递看板同样拆成异步 chunk：新增 tab 不再推高 System 路由 chunk。
const SystemDeliveryTab = asyncTab(() => import('../components/system/SystemDeliveryTab.vue'))
const SystemNotificationTemplatesTab = asyncTab(() => import('../components/system/SystemNotificationTemplatesTab.vue'))

const route = useRoute()
const router = useRouter()

const TAB_NAMES = ['health', 'tokens', 'oplogs', 'alerts', 'experiments', 'feedback', 'funnel', 'event_funnel', 'retention', 'tool_health', 'approvals', 'knowledge', 'orgs', 'sensitivity', 'tasks', 'delivery', 'templates']

const activeTab = ref(TAB_NAMES.includes(route.query.tab) ? route.query.tab : 'health')

const syncTabQuery = () => {
  router.replace({ query: { ...route.query, tab: activeTab.value } })
}

const openKnowledgeDocument = (row) => {
  router.push({ path: '/documents', query: { documentId: String(row.id) } })
}

watch(
  () => route.query.tab,
  (value, oldValue) => {
    if (value === oldValue) return
    const nextTab = typeof value === 'string' && TAB_NAMES.includes(value) ? value : 'health'
    if (nextTab !== activeTab.value) {
      activeTab.value = nextTab
    }
  }
)
</script>

<style scoped>
.system-page {
  display: grid;
  gap: var(--space-5);
  min-width: 0;
  max-width: 100%;
  overflow-x: hidden;
}


/* ─── Tabs（方向 B：轻量 tab 条，去掉底座边框/阴影，选中只留主色 + 细下划线）─── */
:deep(.system-tabs > .el-tabs__header) {
  margin: 0;
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-wrap) {
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  max-width: 100%;
  overflow: hidden;
}
/* 底线只做 1px 轨道，选中态由 .el-tabs__active-bar 的下划线承担 */
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-wrap::after) {
  height: 1px;
}
:deep(.system-tabs),
:deep(.system-tabs .el-tabs__content),
:deep(.system-tabs .el-tab-pane),
:deep(.system-tabs .el-card),
:deep(.system-tabs .app-card),
:deep(.system-tabs .el-table) {
  min-width: 0;
  max-width: 100%;
}
:deep(.system-tabs .el-row) {
  max-width: 100%;
}
:deep(.system-tabs .el-table__inner-wrapper) {
  max-width: 100%;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav) {
  gap: 2px;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item) {
  height: 38px;
  padding: 0 var(--space-3);
  border-radius: 0;
  color: var(--color-text-secondary);
  /* 令牌字号最小档是 13px，方向 B 基准稿的 tab 条是 13.5px，这里按基准稿写死 */
  font-size: 13.5px;
  font-weight: 500;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item:hover) {
  color: var(--color-primary);
}
:deep(.system-tabs > .el-tabs__header .el-tabs__item.is-active) {
  background: transparent;
  color: var(--color-primary);
  font-weight: 600;
}
:deep(.system-tabs > .el-tabs__header .el-tabs__active-bar) {
  height: 2px;
  border-radius: 0;
  background: var(--color-primary);
}
/* 溢出滚动时 Element 会给 nav-wrap 留 20px 的箭头区；显式补回，避免箭头压住首尾 tab */
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-wrap.is-scrollable) {
  padding: 0 var(--space-5);
}
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-next:hover),
:deep(.system-tabs > .el-tabs__header .el-tabs__nav-prev:hover) {
  color: var(--color-primary);
}
/* tab 面板首元素补间距：各 tab 首元素有的是 el-card、有的是方向 B 的 .app-card */
:deep(.system-tabs .el-tab-pane > .el-card:first-child),
:deep(.system-tabs .el-tab-pane > .app-card:first-child),
:deep(.system-tabs .el-tab-pane > .el-row:first-child) {
  margin-top: var(--space-5);
}
:deep(.system-tabs .el-card),
:deep(.system-tabs .app-card) {
  border-radius: var(--radius-lg);
}
:deep(.system-tabs .el-card__header) {
  padding-bottom: var(--space-4);
}
:deep(.system-tabs .el-table th) {
  background: var(--color-bg-alt);
}
</style>
