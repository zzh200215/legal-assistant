// System 观测/图表类 tab 的共享异步入口。
// 与 SystemOpsTabs 分开，避免只查 Token/投递时把组织架构、知识库上传等表格代码一起下载。
export { default as SystemExperimentsTab } from './SystemExperimentsTab.vue'
export { default as PilotAnalyticsTabs } from './PilotAnalyticsTabs.vue'
export { default as SystemEventFunnelTab } from './SystemEventFunnelTab.vue'
export { default as SystemTokensTab } from './SystemTokensTab.vue'
export { default as SystemDeliveryTab } from './SystemDeliveryTab.vue'
export { default as SystemNotificationTemplatesTab } from './SystemNotificationTemplatesTab.vue'
