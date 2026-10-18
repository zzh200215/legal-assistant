import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 右侧「法律助手」（copilot）上下文推导：把「用户现在在看什么」翻译成面板要展示的
// 模块名与案件绑定。
//   - deriveCopilotContext：纯函数（路由快照 → 上下文对象），可单测，无副作用；
//   - useCopilotContext：薄 composable，只负责把当前路由喂进纯函数。
// 案件绑定只从 URL query 推导，不为此新增任何接口请求：拿不到案件名就显示「案件 #id」。

// 「法律咨询」没有独立路由，它是 /legal-workspace 的案件子 tab
// （?case_id=<id>&tab=consultation），因此模块名按 tab 推导。
const CASE_TAB_LABELS = {
  overview: '案件概览',
  documents: '文档',
  consultation: '法律咨询',
  contract: '合同审查',
  draft: '文书',
  review: '审核',
  tasks: '任务',
  deadlines: '关键日期',
  sources: '法律资料',
  billing: '计时计费',
  contracts: '合同台账',
  portal: '客户门户',
}

// /legal-workspace?view=<x> 是非案件视图（案件台账 / 法律检索 / 审核队列）
const WORKSPACE_VIEW_LABELS = {
  cases: '案件台账',
  research: '法律检索',
  review: '审核队列',
}

const PATH_LABELS = {
  '/documents': '文档中心',
  '/tasks': '任务中心',
  '/chat': '智能问答',
  '/agent': 'Agent 工作台',
  '/notifications': '消息通知',
  '/system': '平台状态',
  '/management': '管理中心',
  '/legal-developer': '法务开发者',
  '/legal-onboarding': '使用指南',
  '/pricing': '套餐与计费',
}

const UNKNOWN_MODULE_LABEL = '当前页面'

// 面板可以带进提问的「当前对象」：合同审查 / 文书草稿各自把选中项 id 写进 URL query，
// 面板据此把它交给后端（后端只接受调用者有权访问的对象，取不到就不带）。
// queryKey 是页面写在 URL 上的键，requestKey 是咨询接口的字段名（合同审查接口里叫
// contract_review_id，两者不一致是既有接口约定，别在这里“统一”成同一个名字）。
// 只登记这两个：其它 tab 没有「单一选中对象」的语义，不硬凑。
const ITEM_KINDS = {
  contract: { queryKey: 'contract_id', requestKey: 'contract_review_id', prefix: '合同' },
  draft: { queryKey: 'draft_id', requestKey: 'draft_id', prefix: '文书' },
}

/**
 * query 值可能是 string | number | Array | null：只接受可解析为「正整数」的标量。
 * 无法解析返回 null（面板据此显示未绑定案件的解释，而不是伪造一个 id）。
 */
export function normalizeCaseId(value) {
  const raw = Array.isArray(value) ? value[0] : value
  if (typeof raw === 'number') return Number.isInteger(raw) && raw > 0 ? raw : null
  if (typeof raw !== 'string' || !raw.trim()) return null
  const parsed = Number(raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

/**
 * 路由快照 → copilot 上下文。输入形如 `{ path, query }`（vue-router 的 route 即可）。
 * @returns {{ path: string, moduleLabel: string, caseId: number|null, hasCase: boolean,
 *   caseLabel: string, tab: string, view: string, isLegalWorkspace: boolean, isCaseConsultation: boolean }}
 */
export function deriveCopilotContext(route = {}) {
  const path = typeof route?.path === 'string' ? route.path : ''
  const query = route?.query && typeof route.query === 'object' ? route.query : {}
  const tab = typeof query.tab === 'string' ? query.tab : ''
  const view = typeof query.view === 'string' ? query.view : ''
  const caseId = normalizeCaseId(query.case_id)
  const isLegalWorkspace = path === '/legal-workspace'

  let moduleLabel = PATH_LABELS[path] || UNKNOWN_MODULE_LABEL
  if (isLegalWorkspace) {
    // 与 LegalWorkspace.vue 的取值一致：view 优先，其次是案件子 tab，都没有则是工作台首页
    if (view) moduleLabel = WORKSPACE_VIEW_LABELS[view] || '法律工作台'
    else if (tab) moduleLabel = CASE_TAB_LABELS[tab] || '案件工作台'
    else moduleLabel = '工作台首页'
  }

  // 当前对象只在对应 tab 下认；换 tab / 换案件时由页面把 query 清掉，面板随之不再带。
  const itemKind = isLegalWorkspace && !view && ITEM_KINDS[tab] ? tab : ''
  const itemId = itemKind ? normalizeCaseId(query[ITEM_KINDS[itemKind].queryKey]) : null

  return {
    path,
    moduleLabel,
    caseId,
    hasCase: caseId !== null,
    caseLabel: caseId === null ? '' : `案件 #${caseId}`,
    tab,
    view,
    isLegalWorkspace,
    isCaseConsultation: isLegalWorkspace && !view && tab === 'consultation',
    itemKind,
    itemId,
    hasItem: itemId !== null,
    itemLabel: itemId === null ? '' : `${ITEM_KINDS[itemKind].prefix} #${itemId}`,
  }
}

/**
 * 上下文 → 提问请求体里额外带的字段。没有当前对象时返回空对象
 * （咨询场景的请求体必须保持 `{ question, case_id }` 原样，有 e2e 全等断言）。
 */
export function copilotContextPayload(context) {
  if (!context || !context.itemKind || !context.itemId) return {}
  const { requestKey } = ITEM_KINDS[context.itemKind] || {}
  return requestKey ? { [requestKey]: context.itemId } : {}
}

/** 当前路由的 copilot 上下文（响应式）。路由可注入，便于测试。 */
export function useCopilotContext(routeSource) {
  const source = routeSource || useRoute()
  return computed(() => deriveCopilotContext({ path: source.path, query: source.query }))
}
