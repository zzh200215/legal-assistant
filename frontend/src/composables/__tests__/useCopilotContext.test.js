import test from 'node:test'
import assert from 'node:assert/strict'
import { reactive } from 'vue'
import { deriveCopilotContext, copilotContextPayload, normalizeCaseId, useCopilotContext } from '../useCopilotContext.js'

const WORKSPACE = '/legal-workspace'

test('有 case_id：绑定案件并给出展示标签', () => {
  const ctx = deriveCopilotContext({ path: WORKSPACE, query: { case_id: '12', tab: 'consultation' } })
  assert.equal(ctx.caseId, 12)
  assert.equal(ctx.hasCase, true)
  assert.equal(ctx.caseLabel, '案件 #12')
  assert.equal(ctx.moduleLabel, '法律咨询')
  assert.equal(ctx.isCaseConsultation, true)
})

test('无 case_id：不伪造案件（hasCase=false，标签为空）', () => {
  const ctx = deriveCopilotContext({ path: WORKSPACE, query: { tab: 'consultation' } })
  assert.equal(ctx.caseId, null)
  assert.equal(ctx.hasCase, false)
  assert.equal(ctx.caseLabel, '')
  assert.equal(ctx.moduleLabel, '法律咨询')
})

test('案件咨询路由：仅 /legal-workspace 且 tab=consultation 时为真', () => {
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { tab: 'consultation' } }).isCaseConsultation, true)
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { tab: 'contract' } }).isCaseConsultation, false)
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: {} }).isCaseConsultation, false)
  // view 视图优先于 tab：带 view 时不是案件咨询上下文
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { view: 'cases', tab: 'consultation' } }).isCaseConsultation, false)
  assert.equal(deriveCopilotContext({ path: '/documents', query: { tab: 'consultation' } }).isCaseConsultation, false)
})

test('非案件路由：按模块名映射，案件绑定仍取自 URL', () => {
  assert.equal(deriveCopilotContext({ path: '/documents', query: {} }).moduleLabel, '文档中心')
  assert.equal(deriveCopilotContext({ path: '/tasks', query: { case_id: '7' } }).moduleLabel, '任务中心')
  assert.equal(deriveCopilotContext({ path: '/chat', query: { case_id: '7' } }).caseId, 7)
  assert.equal(deriveCopilotContext({ path: '/unknown-page', query: {} }).moduleLabel, '当前页面')
  assert.equal(deriveCopilotContext({}).moduleLabel, '当前页面')
})

test('工作台子视图/子 tab 的模块名', () => {
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { view: 'research' } }).moduleLabel, '法律检索')
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { case_id: '3', tab: 'draft' } }).moduleLabel, '文书')
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: {} }).moduleLabel, '工作台首页')
})

test('normalizeCaseId：过滤非法与越界输入', () => {
  assert.equal(normalizeCaseId(5), 5)
  assert.equal(normalizeCaseId('5'), 5)
  assert.equal(normalizeCaseId(['5', '6']), 5)
  assert.equal(normalizeCaseId(' 5 '), 5)
  assert.equal(normalizeCaseId(''), null)
  assert.equal(normalizeCaseId('   '), null)
  assert.equal(normalizeCaseId('abc'), null)
  assert.equal(normalizeCaseId('0'), null)
  assert.equal(normalizeCaseId('-3'), null)
  assert.equal(normalizeCaseId('1.5'), null)
  assert.equal(normalizeCaseId(null), null)
  assert.equal(normalizeCaseId(undefined), null)
  assert.equal(normalizeCaseId({}), null)
  assert.equal(normalizeCaseId(0), null)
})

test('当前对象：合同审查 / 文书 tab 下认 URL 里的选中项', () => {
  const contract = deriveCopilotContext({ path: WORKSPACE, query: { case_id: '3', tab: 'contract', contract_id: '12' } })
  assert.equal(contract.itemKind, 'contract')
  assert.equal(contract.itemId, 12)
  assert.equal(contract.hasItem, true)
  assert.equal(contract.itemLabel, '合同 #12')

  const draft = deriveCopilotContext({ path: WORKSPACE, query: { case_id: '3', tab: 'draft', draft_id: '7' } })
  assert.equal(draft.itemKind, 'draft')
  assert.equal(draft.itemLabel, '文书 #7')
})

test('当前对象：只在对应 tab 下生效，其它 tab / 视图 / 非法 id 一律不带', () => {
  // 换到咨询 tab 时 contract_id 不认（页面切换 tab 也会清 query，这里是第二道保险）
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { tab: 'consultation', contract_id: '12' } }).hasItem, false)
  // 没有 tab、或非案件视图时不认
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { contract_id: '12' } }).hasItem, false)
  assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { view: 'cases', contract_id: '12' } }).hasItem, false)
  // 非法 id
  for (const bad of ['0', 'abc', '1.5', '', null, undefined]) {
    assert.equal(deriveCopilotContext({ path: WORKSPACE, query: { tab: 'contract', contract_id: bad } }).hasItem, false)
  }
})

test('copilotContextPayload：只带一个 id 字段，没有对象时为空对象', () => {
  const contract = deriveCopilotContext({ path: WORKSPACE, query: { tab: 'contract', contract_id: '12' } })
  // 接口字段名与 URL 键不同：合同审查接口里叫 contract_review_id
  assert.deepEqual(copilotContextPayload(contract), { contract_review_id: 12 })
  const draft = deriveCopilotContext({ path: WORKSPACE, query: { tab: 'draft', draft_id: '7' } })
  assert.deepEqual(copilotContextPayload(draft), { draft_id: 7 })
  // 咨询场景必须保持 { question, case_id } 原样：这里不能加任何字段
  const consult = deriveCopilotContext({ path: WORKSPACE, query: { case_id: '3', tab: 'consultation' } })
  assert.deepEqual(copilotContextPayload(consult), {})
  assert.deepEqual(copilotContextPayload(null), {})
})

test('useCopilotContext：随路由变化重算', () => {
  const route = reactive({ path: '/documents', query: {} })
  const ctx = useCopilotContext(route)
  assert.equal(ctx.value.moduleLabel, '文档中心')
  assert.equal(ctx.value.hasCase, false)

  route.path = WORKSPACE
  route.query = { case_id: '9', tab: 'consultation' }
  assert.equal(ctx.value.moduleLabel, '法律咨询')
  assert.equal(ctx.value.caseLabel, '案件 #9')
  assert.equal(ctx.value.isCaseConsultation, true)

  route.query = { view: 'cases' }
  assert.equal(ctx.value.moduleLabel, '案件台账')
  assert.equal(ctx.value.hasCase, false)
})
