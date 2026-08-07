import { test, expect } from '@playwright/test'

// 订阅购买流程联调回归：
//   方案列表 + 当前方案 → 升级按钮 → POST /billing/subscriptions/checkout?tier=pro
//   → 支付网关未配置（configured:false）→ 页面展示 warning 提示。
// 全 mock，锁定 checkout 契约与未配置网关的错误路径 UX。
const success = (data) => ({ success: true, data })

const PLANS = [
  { id: 1, name: '免费版', tier: 'free', price_monthly: 0, quota_consultation: 5, quota_review: 2, quota_draft: 2, description: '个人试用' },
  { id: 2, name: '专业版', tier: 'pro', price_monthly: 99, quota_consultation: 100, quota_review: 50, quota_draft: 50, description: '成长团队' },
  { id: 3, name: '团队版', tier: 'team', price_monthly: 299, quota_consultation: 500, quota_review: 200, quota_draft: 200, description: '规模化律所' },
]

test('订阅购买：方案列表 + checkout 未配置网关提示', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'pricing-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active' }) })
    if (method === 'GET' && path === '/billing/plans') return route.fulfill({ json: success(PLANS) })
    if (method === 'GET' && path === '/billing/subscriptions/me') return route.fulfill({ json: success({ plan: { tier: 'free', name: '免费版', status: 'active' } }) })
    if (method === 'POST' && path === '/billing/subscriptions/checkout') {
      requests.checkout = url.searchParams.get('tier')
      return route.fulfill({ json: success({ checkout_url: null, tier: url.searchParams.get('tier'), message: '支付网关尚未配置，请联系管理员设置 PAYMENT_CHECKOUT_BASE_URL', configured: false }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/pricing')

  // 方案列表 + 当前方案（免费版当前，禁用）
  await expect(page.getByText('免费版').first()).toBeVisible()
  await expect(page.getByText('专业版').first()).toBeVisible()
  await expect(page.getByText('团队版').first()).toBeVisible()
  await expect(page.getByText('当前方案：免费版（active）')).toBeVisible()
  await expect(page.getByRole('button', { name: '当前方案', exact: true })).toBeDisabled()

  // 升级专业版 → checkout 未配置 → warning 提示 + POST 载荷
  const proCard = page.locator('.plan-card', { hasText: '专业版' })
  await proCard.getByRole('button', { name: '升级', exact: true }).click()
  await expect(page.getByText('支付网关尚未配置，请联系管理员设置 PAYMENT_CHECKOUT_BASE_URL')).toBeVisible()
  await expect.poll(() => requests.checkout, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.checkout).toBe('pro')

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
