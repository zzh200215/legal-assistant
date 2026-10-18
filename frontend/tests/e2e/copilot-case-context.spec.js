import { test, expect } from '@playwright/test'

// 「右栏助手带上当前正在看的合同审查 / 文书草稿」联调回归（全 mock，不依赖后端）：
//   合同审查 tab 把选中项写进 URL → 面板上下文条显示「合同 #12」→ 提问请求体带 contract_id；
//   文书 tab 同理（draft_id）；咨询 tab 没有当前对象时，请求体形状必须保持 { question, case_id } 不变。
// 客户端只传 id，正文由后端按 id 读取并做权限校验。

const success = (data) => ({ success: true, data })

const ME = { id: 15, username: 'pilot01-lawyer', role: 'user', organization_id: 5, department_id: null, status: 'active' }
const CASE = {
  id: 3, title: '张三劳动合同纠纷', case_type: 'labor_dispute', status: 'in_progress',
  description: '试用期辞退争议', owner_name: '王律师', client_name: '张三', opposing_party: '甲公司',
  updated_at: '2026-08-06T09:00:00', created_at: '2026-08-01T09:00:00',
  item_counts: { consultations: 1, contract_reviews: 1, drafts: 1 },
}
const CONTRACT = {
  id: 12, case_id: 3, title: '服务合同（甲方模板）', status: 'reviewed', version: 1,
  content: '甲方应在 30 日内付款。', risks: [],
  created_at: '2026-08-05T09:00:00', updated_at: '2026-08-06T09:00:00',
}
const DRAFT = {
  id: 7, case_id: 3, title: '劳动仲裁申请书', document_type: 'labor_arbitration_application',
  status: 'draft', content: '申请人：张三', fields: {},
  created_at: '2026-08-05T09:00:00', updated_at: '2026-08-06T09:00:00',
}
const QUESTION = '这份合同里逾期付款的责任怎么约定？'
const ANSWER = {
  id: 88, case_id: 3, question: QUESTION, category: 'contract_review', known_facts: [], missing_facts: [],
  advice: '合同应明确逾期付款违约金标准。', risk_level: 'low', status: 'draft', confidence: 80,
  feedback_score: null, created_at: '2026-08-07T10:00:00', references: [],
}

async function mockApi(page) {
  const consultPosts = []
  await page.addInitScript(() => localStorage.setItem('token', 'copilot-ctx-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, organization_name: '示例律所' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([CASE]) })
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [CONTRACT], drafts: [DRAFT] }) })
    if (method === 'GET' && path === '/legal/matters/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [CONTRACT], drafts: [DRAFT] }) })
    if (method === 'GET' && path === '/legal/matters/3/activity') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-queue') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/legal/consultations') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success(null) })
    if (method === 'GET' && path === '/legal/contract-reviews') return route.fulfill({ json: success([CONTRACT]) })
    if (method === 'GET' && path === '/legal/contract-reviews/12/versions') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/contract-reviews/12/risk-items') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/drafts') return route.fulfill({ json: success([DRAFT]) })
    if (method === 'GET' && path === '/legal/document-templates') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/drafts/7/versions') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/drafts/7/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/drafts/7/collaboration') return route.fulfill({ json: success({ collaborators: [] }) })
    if (method === 'POST' && path === '/legal/consultations') {
      const body = route.request().postDataJSON()
      consultPosts.push(body)
      return route.fulfill({ json: success(ANSWER) })
    }
    return route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  return { consultPosts }
}

const dockSelector = '.copilot-dock'

const ask = async (page, dock) => {
  await dock.locator('.cop-textarea').fill(QUESTION)
  await dock.locator('.cop-textarea').press('Enter')
}

test('合同审查 tab：选中项写进 URL，提问带上 contract_id', async ({ page }) => {
  const { consultPosts } = await mockApi(page)

  await page.goto('/legal-workspace?case_id=3&tab=contract')
  const dock = page.locator(dockSelector)
  await expect(dock).toBeVisible()

  // 页面把「当前正在审查的合同」发布到 URL（右栏助手据此知道用户在看哪一份）
  await expect.poll(() => new URL(page.url()).searchParams.get('contract_id')).toBe('12')
  await expect(dock.locator('.ctx')).toContainText('合同审查')
  await expect(dock.locator('.ctx')).toContainText('合同 #12')

  await ask(page, dock)
  await expect.poll(() => consultPosts.length).toBe(1)
  // 客户端只传 id（接口字段名 contract_review_id）：正文由后端按 id 读取并做权限校验
  expect(consultPosts[0]).toEqual({ question: QUESTION, case_id: 3, contract_review_id: 12 })

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('文书 tab：选中项写进 URL，面板上下文条显示文书 #7', async ({ page }) => {
  await mockApi(page)

  await page.goto('/legal-workspace?case_id=3&tab=draft')
  const dock = page.locator(dockSelector)
  await expect(dock).toBeVisible()

  await expect.poll(() => new URL(page.url()).searchParams.get('draft_id')).toBe('7')
  await expect(dock.locator('.ctx')).toContainText('文书 #7')

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('咨询 tab：没有当前对象时请求体形状不变', async ({ page }) => {
  const { consultPosts } = await mockApi(page)

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await expect(dock).toBeVisible()
  await expect(dock.locator('.ctx')).toContainText('法律咨询')
  await expect(dock.locator('.ctx')).not.toContainText('#12')

  await ask(page, dock)
  await expect.poll(() => consultPosts.length).toBe(1)
  expect(consultPosts[0]).toEqual({ question: QUESTION, case_id: 3 })
})
