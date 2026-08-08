import { test, expect } from '@playwright/test'

// 费用通知单状态机联调回归：发送 POST /send → 作废 POST /void?reason=
// → 收款 POST /payments → 退款 POST /refunds → 详情 GET /payments。
// 全 mock，锁定发票生命周期契约（send 走外发审批流、void 的 reason 为 query 参数）。
const success = (data) => ({ success: true, data })

const INVOICES = [
  { id: 1, invoice_no: 'INV-001', client_display_name: '草稿客户', total_amount: 100, status: 'draft', billing_period_start: null, billing_period_end: null },
  { id: 2, invoice_no: 'INV-002', client_display_name: '已发客户', total_amount: 200, status: 'sent', billing_period_start: '2026-07-01', billing_period_end: '2026-07-31' },
  { id: 3, invoice_no: 'INV-003', client_display_name: '已付客户', total_amount: 300, status: 'paid', billing_period_start: null, billing_period_end: null },
]

test('发票状态机：发送/作废/收款/退款/详情', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'invoice-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })

    // 计时计费 tab
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/time-entries') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/legal/orgs/5/billing-rules') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/orgs/5/invoices') return route.fulfill({ json: success({ items: INVOICES, total: 3, page: 1, page_size: 20 }) })

    // 发票状态机
    if (method === 'POST' && path === '/legal/invoices/1/send') {
      requests.send = true
      return route.fulfill({ json: success({ id: 1, status: 'sent' }) })
    }
    if (method === 'POST' && path === '/legal/invoices/1/void') {
      requests.voidReason = url.searchParams.get('reason')
      return route.fulfill({ json: success({ id: 1, status: 'voided' }) })
    }
    if (method === 'POST' && path === '/legal/invoices/2/payments') {
      requests.payment = request.postDataJSON()
      return route.fulfill({ json: success({ id: 11, invoice_id: 2, amount: requests.payment.amount, payment_method: requests.payment.payment_method, note: requests.payment.note }) })
    }
    if (method === 'POST' && path === '/legal/invoices/3/refunds') {
      requests.refund = request.postDataJSON()
      return route.fulfill({ json: success({ id: 21, invoice_id: 3, amount: requests.refund.amount, reason: requests.refund.reason }) })
    }
    if (method === 'GET' && path === '/legal/invoices/1/payments') {
      requests.detail = true
      return route.fulfill({ json: success([{ id: 31, invoice_id: 1, amount: 100, payment_method: 'bank_transfer', note: '首期款', created_at: '2026-08-01T10:00:00' }]) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  // 登录 → 工作台 → 计时计费 tab
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.getByRole('tab', { name: '计时计费', exact: true }).click()

  // 发票列表渲染
  await expect(page.getByText('草稿客户')).toBeVisible()
  await expect(page.getByText('已发客户')).toBeVisible()
  await expect(page.getByText('已付客户')).toBeVisible()

  const draftRow = page.locator('.el-table__row', { hasText: '草稿客户' })
  const sentRow = page.locator('.el-table__row', { hasText: '已发客户' })
  const paidRow = page.locator('.el-table__row', { hasText: '已付客户' })

  // 发送：draft → POST /send（走外发审批流）
  await draftRow.getByRole('button', { name: '发送', exact: true }).click()
  await expect(page.getByText('已创建外发草稿，待审批并发送成功后生效')).toBeVisible()
  expect(requests.send).toBe(true)

  // 作废：draft → prompt → POST /void?reason=
  await draftRow.getByRole('button', { name: '作废', exact: true }).click()
  await page.getByPlaceholder('作废原因...').fill('客户取消合作')
  await page.getByRole('button', { name: '确认作废', exact: true }).click()
  await expect.poll(() => requests.voidReason, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.voidReason).toBe('客户取消合作')

  // 收款：sent → dialog → POST /payments
  await sentRow.getByRole('button', { name: '收款', exact: true }).click()
  const payDialog = page.locator('.el-dialog:visible')
  await payDialog.getByRole('button', { name: '确认收款', exact: true }).click()
  await expect.poll(() => requests.payment, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.payment).toEqual({ amount: 200, payment_method: 'bank_transfer', note: '' })

  // 退款：paid → dialog → POST /refunds
  await paidRow.getByRole('button', { name: '退款', exact: true }).click()
  await page.waitForTimeout(500) // 等退款对话框动画稳定
  await page.getByRole('textbox', { name: '退款原因' }).fill('发票有误，客户要求重开')
  await page.getByRole('button', { name: '确认退款', exact: true }).click()
  await expect.poll(() => requests.refund, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.refund).toEqual({ amount: 300, reason: '发票有误，客户要求重开' })

  // 详情：draft → GET /payments → 收款记录
  await draftRow.getByRole('button', { name: '详情', exact: true }).click()
  const detailDialog = page.locator('.el-dialog:visible')
  await expect(detailDialog.getByText('首期款')).toBeVisible()
  expect(requests.detail).toBe(true)

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
