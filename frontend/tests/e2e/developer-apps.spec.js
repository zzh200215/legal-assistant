import { test, expect } from '@playwright/test'

// LegalDeveloper 的 org 解析回归：必须从 /auth/me 取真实 organization_id，
// 绝不回落到硬编码 org 1（此前 localStorage.organization_id 从未被写入，
// 非 org-1 的 admin 必然 403「您不是该组织的成员」）。
const success = (data) => ({ success: true, data })

test('开发者视图从 /auth/me 解析 org，创建应用走正确组织', async ({ page }) => {
  const requests = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'dev-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)

    if (method === 'GET' && path === '/auth/me') {
      await route.fulfill({ json: success({ id: 15, organization_id: 5, role: 'dept_admin' }) })
      return
    }
    if (method === 'GET' && path === '/developer/notifications/me') {
      await route.fulfill({ json: success({ items: [], unread: 0 }) })
      return
    }
    if (method === 'GET' && path === '/developer/orgs/5/apps') {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/developer/orgs/5/operations/summary') {
      await route.fulfill({ json: success({ queued_jobs: 0, failed_jobs: 0, pending_webhooks: 0, failed_webhooks: 0, api_calls: 0, callback_verification_failures: 0 }) })
      return
    }
    if (method === 'POST' && path === '/developer/orgs/5/apps') {
      requests.appCreate = route.request().postDataJSON()
      await route.fulfill({ json: success({ app: { id: 1 }, api_key: 'lzj_op_fake', key_prefix: 'lzj_op_fake' }) })
      return
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled E2E API request: ${method} ${path}` } })
  })

  await page.goto('/legal-developer')
  await page.getByText('创建并显示一次性密钥').waitFor({ timeout: 8000 })

  expect(requests).toContain('GET /developer/orgs/5/apps')
  expect(requests).toContain('GET /developer/orgs/5/operations/summary')
  expect(requests.some((r) => r.includes('/orgs/1/'))).toBe(false)

  // 创建应用必须 POST 到正确组织
  await page.getByPlaceholder('应用名称').fill('回归测试应用')
  await page.getByRole('button', { name: '创建并显示一次性密钥' }).click()
  await expect(page.getByText(/请立即保存密钥：lzj_op_fake/)).toBeVisible()
  expect(requests.appCreate).toEqual({ name: '回归测试应用' })

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
