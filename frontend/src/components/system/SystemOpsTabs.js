// System 运营/表格类 tab 的共享异步入口。
// 这些 tab 都依赖 el-table / el-upload / el-dialog 等较重模块，合并成一个按需 chunk 后：
// 1. 首屏只加载健康检查与概览条；
// 2. 首次打开任一运营 tab 时一次取回，后续 tab 不再重复请求；
// 3. 避免 15 个 tab 各自 chunk 的包装开销把总资产顶过预算。
export { default as SystemTasksTab } from './SystemTasksTab.vue'
export { default as SystemOplogsTab } from './SystemOplogsTab.vue'
export { default as SystemAlertsTab } from './SystemAlertsTab.vue'
export { default as SystemFeedbackTab } from './SystemFeedbackTab.vue'
export { default as SystemToolHealthTab } from './SystemToolHealthTab.vue'
export { default as SystemApprovalsTab } from './SystemApprovalsTab.vue'
export { default as SystemKnowledgeTab } from './SystemKnowledgeTab.vue'
export { default as SystemOrgTab } from './SystemOrgTab.vue'
export { default as SystemSensitivityTab } from './SystemSensitivityTab.vue'
