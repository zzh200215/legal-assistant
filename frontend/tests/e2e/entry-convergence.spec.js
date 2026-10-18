import { test, expect } from '@playwright/test'

// 助手入口收敛（产品契约回归，全 mock）：
//   ① 工作台「案件助手」快捷入口 → 打开右侧面板，不再跳 /chat
//   ② 案件工作台「打开助手」 → 打开面板且案件上下文仍在
//   ③ 面板「整页」 → 进入 /chat 深度视图并带上 case_id
//   ④ 普通用户（role=user）→ Agent 导航可见、/agent 不再 403
// 前三条锁定「三入口归一 + 保留展开整页」，第四条锁定 Agent 入口对非管理员开放。

const success = (data) => ({ success: true, data })

const ME = { id: 15, username: 'pilot01-lawyer', role: 'user', organization_id: 5, department_id: null, status: 'active' }
const CASE = {
  id: 3, title: '张三劳动合同纠纷', case_type: 'labor_dispute', status: 'in_progress',
  description: '试用期辞退争议', owner_name: '王律师', client_name: '张三', opposing_party: '甲公司',
  updated_at: '2026-08-06T09:00:00', created_at: '2026-08-01T09:00:00',
  item_counts: { consultations: 1, contract_reviews: 0, drafts: 0 },
}

async function mockApi(page) {
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'converge-e2e-token'))
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ onboarded_at: '2026-08-01T09:00:00' }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, organization_name: '示例律所', counts: { consultations: 3, contract_reviews: 1 } }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([CASE]) })
    if (method === 'GET' && path === '/legal/review-queue') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/matters/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/matters/3/activity') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/consultations') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success(null) })
    return route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })
  return { failed }
}

test('工作台「案件助手」快捷入口打开右侧面板，不再离开当前页面', async ({ page }) => {
  const { failed } = await mockApi(page)
  await page.goto('/legal-workspace')

  const dock = page.locator('.copilot-dock')
  await expect(dock).toBeVisible()
  // 先收起，模拟用户关掉面板后从快捷入口重新打开
  await page.getByRole('button', { name: '收起法律助手' }).click()
  await expect(dock.locator('.cop-body')).toHaveCount(0)

  await page.locator('.app-thread', { hasText: '案件助手' }).click()
  await expect(page).toHaveURL(/\/legal-workspace$/)
  await expect(dock.locator('.cop-body')).toBeVisible()
  expect(failed).toEqual([])
})

test('案件工作台「打开助手」打开面板并保留案件上下文', async ({ page }) => {
  const { failed } = await mockApi(page)
  await page.goto('/legal-workspace?case_id=3&tab=overview')

  const dock = page.locator('.copilot-dock')
  await page.getByRole('button', { name: '收起法律助手' }).click()
  await expect(dock.locator('.cop-body')).toHaveCount(0)

  await page.getByRole('button', { name: '打开助手' }).click()
  await expect(page).toHaveURL(/case_id=3/)
  await expect(dock.locator('.cop-body')).toBeVisible()
  await expect(dock.locator('.ctx')).toContainText('案件 #3')
  expect(failed).toEqual([])
})

test('面板「整页」进入深度视图并带上案件', async ({ page }) => {
  await mockApi(page)
  await page.goto('/legal-workspace?case_id=3&tab=overview')

  await page.getByRole('button', { name: '在整页中打开案件助手' }).click()
  await expect(page).toHaveURL(/\/chat\?case_id=3$/)
})

// 普通用户（role=user）的 Agent 入口：导航可见 + 路由门禁放行。
// 本用例不校验 failed responses——/agent/* 的真实响应形状未 mock，页面自身的请求会 500。
test('普通用户能看到 Agent 入口并进入 /agent（不再 403）', async ({ page }) => {
  await mockApi(page)
  await page.goto('/legal-workspace')

  const navEntry = page.locator('.top-nav .nav-entry', { hasText: 'Agent' })
  await expect(navEntry).toBeVisible()
  await navEntry.click()
  await expect(page).toHaveURL(/\/agent$/)
  await expect(page.locator('.forbidden-state')).toHaveCount(0)
})
