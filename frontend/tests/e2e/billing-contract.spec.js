import { test, expect } from '@playwright/test'

// 计费载荷契约回归（取代已过时的 legal-workflow.spec.js）：
//   1. 计时器暂停用 { action: 'pause' }，绝不发送旧字段 status（曾导致 422）
//   2. 发票载荷用 client_display_name + issue_date，绝不发送旧字段 client_name
// 全 mock，确定性，不依赖真实后端/LLM。
const success = (data) => ({ success: true, data })

const RUNNING_ENTRY = {
  id: 501,
  description: '联调契约计时',
  status: 'running',
  billable: null,
  started_at: '2026-08-07T09:00:00',
  created_at: '2026-08-07T09:00:00',
}

test('计费契约：计时器暂停用 action 载荷、发票用 client_display_name + issue_date', async ({ page }) => {
  const requests = {}
  const unexpectedApiRequests = []
  const failedResponses = []

  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      failedResponses.push(`${response.request().method()} ${response.url()} -> ${response.status()}`)
    }
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'POST' && path === '/auth/login') {
      await route.fulfill({ json: success({ access_token: 'billing-e2e-token' }) })
      return
    }
    if (method === 'GET' && path === '/auth/me') {
      await route.fulfill({ json: success({ id: 7, username: 'pilot_lawyer', email: 'pilot@example.com', role: 'user', status: 'active' }) })
      return
    }
    if (method === 'GET' && path === '/developer/onboarding') {
      await route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
      return
    }

    if (request.headers().authorization !== 'Bearer billing-e2e-token') {
      unexpectedApiRequests.push(`missing authorization: ${method} ${path}`)
      await route.fulfill({ status: 401, json: { detail: 'missing authorization' } })
      return
    }

    if (method === 'GET' && path === '/legal/overview') {
      await route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases') {
      await route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
      return
    }
    if (method === 'GET' && path === '/billing/subscriptions/quota') {
      await route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
      return
    }

    // 工作台挂载时非 lazy tab 会加载的集合
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/legal/review-stats') {
      await route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
      return
    }

    // 计时计费 tab：运行中的计时 + 规则 + 发票列表
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/time-entries') {
      await route.fulfill({ json: success({ items: [RUNNING_ENTRY], total: 1, page: 1, page_size: 20 }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/billing-rules') {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/invoices') {
      await route.fulfill({ json: success({ items: [], total: 0 }) })
      return
    }

    // 计时器暂停：断言用 action 而非 status
    if (method === 'PATCH' && path === '/legal/time-entries/501') {
      requests.timerPause = request.postDataJSON()
      await route.fulfill({ json: success({ ...RUNNING_ENTRY, status: 'paused' }) })
      return
    }

    // 发票创建：断言 client_display_name + issue_date
    if (method === 'POST' && path === '/legal/orgs/5/invoices') {
      requests.invoice = request.postDataJSON()
      await route.fulfill({ json: success({ id: 701, client_display_name: requests.invoice.client_display_name, issue_date: requests.invoice.issue_date, status: 'draft' }) })
      return
    }

    unexpectedApiRequests.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled E2E API request: ${method} ${path}` } })
  })

  // 登录 → 工作台 → 选案件 3
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot_lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.locator('.case-bar .el-select').click()
  await page.getByRole('option', { name: /联调测试案件/ }).click()

  // 计时器：运行中的条目显示「暂停」，点击后必须发 action 载荷
  await page.getByRole('tab', { name: '计时计费', exact: true }).click()
  const timerCard = page.locator('.timer-running')
  await expect(timerCard.getByText('联调契约计时')).toBeVisible()
  await timerCard.getByRole('button', { name: '暂停', exact: true }).click()
  await expect.poll(() => requests.timerPause).not.toBeNull()
  expect(requests.timerPause).toEqual({ action: 'pause' })
  expect(requests.timerPause).not.toHaveProperty('status')
  await expect(timerCard.getByRole('button', { name: '继续', exact: true })).toBeVisible()

  // 发票：创建费用通知单，载荷必须用 client_display_name + issue_date
  await page.getByRole('button', { name: '创建费用通知单', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建费用通知单' })
  await dialog.getByPlaceholder('客户名称').fill('测试客户有限公司')
  await dialog.getByPlaceholder('YYYY-MM-DD').first().fill('2026-08-07')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect.poll(() => requests.invoice).not.toBeNull()
  expect(requests.invoice).toMatchObject({
    client_display_name: '测试客户有限公司',
    issue_date: '2026-08-07',
    billing_period_start: null,
    billing_period_end: null,
    case_id: 3,
  })
  expect(requests.invoice).not.toHaveProperty('client_name')
  expect(requests.invoice).not.toHaveProperty('invoice_date')

  expect(unexpectedApiRequests).toEqual([])
  expect(failedResponses).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
