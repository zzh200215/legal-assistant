import { test, expect } from '@playwright/test'

// System 组织架构 tab（仅全局 admin）联调回归：
//   admin 挂载（12+ 系统端点全部健康）→ 组织/部门/用户列表渲染 →
//   创建组织 POST → 创建部门 POST（选组织）→ 用户归属分配 POST。
// 全 mock，锁定 /org/* 与 admin 权限分支接线。
const success = (data) => ({ success: true, data })

const ORGS = [
  { id: 1, name: '试点律所01', code: 'PILOT-01', description: '试点一' },
  { id: 5, name: '试点律所02', code: 'PILOT-05', description: '试点二' },
]
const DEPTS = [{ id: 51, organization_id: 5, name: '诉讼部', code: 'LIT', description: '诉讼业务' }]
const USERS = [
  { id: 3, username: 'admin', role: 'admin', organization_id: 1, department_id: null, job_title: null },
  { id: 15, username: 'pilot01-lawyer', role: 'dept_admin', organization_id: 5, department_id: 51, job_title: '审核律师' },
]

test('System 组织架构：admin 列表 + 创建组织/部门 + 归属分配', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'system-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 3, username: 'admin', role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })

    // System 挂载健康端点
    if (method === 'GET' && path === '/health') return route.fulfill({ json: success({ status: 'ok', timestamp: '2026-08-07T10:00:00', checks: { database: { status: 'ok' }, redis: { status: 'ok' }, llm_provider: { status: 'ok' } } }) })
    if (method === 'GET' && path === '/analytics/tokens/my-stats') return route.fulfill({ json: success({ by_action: {}, by_date: {}, by_model: {}, governance: { today: {}, rate_limit: {}, policy: {} } }) })
    if (method === 'GET' && path === '/analytics/tokens/global-stats') return route.fulfill({ json: success({ by_model: {}, governance: { today: {}, policy: {} } }) })
    if (method === 'GET' && path === '/analytics/llm-calls/stats') return route.fulfill({ json: success({ total_calls: 0, failed_calls: 0, avg_duration_ms: 0, success_rate: 0, by_module: {}, by_action: {}, failed_by_date: {} }) })
    if (method === 'GET' && path === '/analytics/llm-calls') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/llm-billing/stats') return route.fulfill({ json: success({ summary: {}, by_model: {}, pricing: { currency: 'CNY', items: [] } }) })
    if (method === 'GET' && path === '/analytics/llm-pricing') return route.fulfill({ json: success({ currency: 'CNY', items: [] }) })
    if (method === 'GET' && path === '/analytics/qa-replays') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/analytics/oplogs') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/oplogs/stats') return route.fulfill({ json: success({ total_operations: 0, by_module: {} }) })
    if (method === 'GET' && path === '/analytics/alerts') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/alerts/stats') return route.fulfill({ json: success({ total: 0, by_source: {}, by_category: {}, by_date: {} }) })
    if (method === 'GET' && path === '/analytics/feedback') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/feedback/stats') return route.fulfill({ json: success({ total_feedback: 0, positive_count: 0, open_count: 0, resolution_rate: 0, by_reason: {}, by_date: {} }) })
    if (method === 'GET' && path === '/analytics/tool-health') return route.fulfill({ json: success({ items: [] }) })
    if (method === 'GET' && path === '/analytics/task-runs') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/experiments/overview') return route.fulfill({ json: success({ summary: {}, artifact_status: {}, rollouts: { items: [] }, experiments: [], prompt_traffic: { items: [] } }) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/documents/knowledge-bases') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/documents/') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/connectors/') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/connectors/sync-jobs') return route.fulfill({ json: success([]) })

    if (method === 'GET' && path === '/admin/funnel') return route.fulfill({ json: success({ data: { funnel: [], cohort: { registered: 0 } } }) })
    if (method === 'GET' && path === '/admin/retention') return route.fulfill({ json: success({ data: {} }) })
    if (method === 'GET' && path === '/admin/north-star') return route.fulfill({ json: success({ data: {} }) })
    // 组织架构
    if (method === 'GET' && path === '/org/organizations') return route.fulfill({ json: success(ORGS) })
    if (method === 'GET' && path === '/org/departments') return route.fulfill({ json: success(DEPTS) })
    if (method === 'GET' && path === '/auth/users') return route.fulfill({ json: success(USERS) })
    if (method === 'POST' && path === '/org/organizations') {
      requests.orgCreate = request.postDataJSON()
      return route.fulfill({ json: success({ id: 9, ...requests.orgCreate }) })
    }
    if (method === 'POST' && path === '/org/departments') {
      requests.deptCreate = request.postDataJSON()
      return route.fulfill({ json: success({ id: 61, ...requests.deptCreate }) })
    }
    if (method === 'POST' && /^\/org\/users\/\d+\/assign$/.test(path)) {
      requests.userAssign = request.postDataJSON()
      return route.fulfill({ json: success({ id: 15, ...requests.userAssign }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/system')
  await page.getByRole('tab', { name: '组织架构', exact: true }).click()

  // admin 分支：列表渲染
  await expect(page.getByText('试点律所01').first()).toBeVisible()
  await expect(page.getByText('诉讼部').first()).toBeVisible()
  await expect(page.getByText('pilot01-lawyer').first()).toBeVisible()

  // 创建组织 → POST
  await page.getByPlaceholder('组织名称').fill('联调冒烟律所')
  await page.getByPlaceholder('组织编码').first().fill('SMOKE-01')
  await page.getByPlaceholder('组织说明').first().fill('联调创建')
  await page.locator('.el-card', { hasText: '创建组织' }).getByRole('button', { name: '创建组织', exact: true }).click()
  await expect.poll(() => requests.orgCreate, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.orgCreate).toEqual({ name: '联调冒烟律所', code: 'SMOKE-01', description: '联调创建' })

  // 创建部门 → POST（选组织）
  await page.locator('.el-card', { hasText: '创建部门' }).locator('.el-select').click()
  await page.getByRole('option', { name: '试点律所02', exact: true }).click()
  await page.waitForTimeout(300)
  await page.locator('.el-card', { hasText: '创建部门' }).getByPlaceholder('部门名称').fill('风控部')
  await page.locator('.el-card', { hasText: '创建部门' }).getByPlaceholder('部门编码').fill('RISK')
  await page.locator('.el-card', { hasText: '创建部门' }).getByRole('button', { name: '创建部门', exact: true }).click()
  await expect.poll(() => requests.deptCreate, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.deptCreate).toEqual({ organization_id: 5, name: '风控部', code: 'RISK', description: '' })

  // 用户归属分配 → POST
  await page.locator('.el-card', { hasText: '用户归属分配' }).locator('.el-select').nth(0).click()
  await page.getByRole('option', { name: /pilot01-lawyer/ }).click()
  await page.waitForTimeout(300)
  await page.locator('.el-card', { hasText: '用户归属分配' }).locator('.el-select').nth(1).click()
  await page.getByRole('option', { name: '试点律所02', exact: true }).click()
  await page.waitForTimeout(300)
  await page.locator('.el-card', { hasText: '用户归属分配' }).locator('.el-select').nth(2).click()
  await page.getByRole('option', { name: '诉讼部', exact: true }).click()
  await page.waitForTimeout(300)
  await page.locator('.el-card', { hasText: '用户归属分配' }).getByPlaceholder('岗位名称').fill('执业律师')
  await page.locator('.el-card', { hasText: '用户归属分配' }).getByRole('button', { name: '保存归属', exact: true }).click()
  await expect.poll(() => requests.userAssign, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.userAssign).toEqual({ organization_id: 5, department_id: 51, job_title: '执业律师' })

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
