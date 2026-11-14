import { test, expect } from '@playwright/test'

// 通知中心：请假提示条、代收来源标签（请假代收 / 类型代收 / 历史代收）、来源筛选按来源细分。
// 全 mock，锁定列表字段与筛选参数契约。
const success = (data) => ({ success: true, data })
const ME = { id: 15, username: 'pilot01-lawyer', role: 'dept_admin', organization_id: 5, department_id: 51, status: 'active' }
const LEAVE = {
  delegate_user_id: 16,
  delegate_name: '协办律师',
  leave_start: '2026-11-10',
  leave_end: '2026-11-20',
  note: '出差',
  timezone: 'Asia/Shanghai',
  status: 'active',
  days_left: 9,
  updated_at: '2026-11-11T09:00:00',
}
const ITEMS = [
  {
    id: 3,
    title: '合同到期提醒',
    body: '合同 30 天后到期',
    event_type: 'deadline',
    status: 'delivered',
    created_at: '2026-11-14T09:00:00',
    on_behalf_of_user_id: 15,
    on_behalf_of_source: 'leave',
    on_behalf_of_name: '主办律师',
  },
  {
    id: 2,
    title: '审核待处理',
    body: '有 1 条审核待处理',
    event_type: 'approval',
    status: 'delivered',
    created_at: '2026-11-14T08:00:00',
    on_behalf_of_user_id: 15,
    on_behalf_of_source: 'event',
    on_behalf_of_name: '主办律师',
  },
  {
    id: 1,
    title: '本人通知',
    body: '发给本人的提醒',
    event_type: 'deadline',
    status: 'delivered',
    created_at: '2026-11-14T07:00:00',
    on_behalf_of_user_id: null,
    on_behalf_of_source: null,
    on_behalf_of_name: null,
  },
]

test('通知中心：请假提示条 + 代收来源标签 + 来源筛选', async ({ page }) => {
  const listQueries = []
  // 顶部铃铛也会请求同一个列表接口（不带分页参数），只看通知中心自己的请求
  const pageQueries = () => listQueries.filter((query) => query.page_size === '20')
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'center-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notification-leave/me') {
      return route.fulfill({ json: success(LEAVE) })
    }
    if (method === 'GET' && path === '/developer/notifications/me') {
      listQueries.push(Object.fromEntries(url.searchParams))
      const delegated = url.searchParams.get('delegated')
      const source = url.searchParams.get('delegation_source')
      const items = ITEMS.filter((item) => {
        if (delegated === 'true' && !item.on_behalf_of_user_id) return false
        if (delegated === 'false' && item.on_behalf_of_user_id) return false
        if (source && item.on_behalf_of_source !== source) return false
        return true
      })
      return route.fulfill({
        json: success({ items, unread: items.length, total: items.length, page: 1, page_size: 20 }),
      })
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: '通知中心' })).toBeVisible()

  // 请假提示条：直接给出代理与区间，可跳到通知偏好
  const banner = page.locator('.notification-leave')
  await expect(banner).toContainText('请假中')
  await expect(banner.locator('.notification-leave-text')).toHaveText(
    '代 协办律师 接收 · 2026-11-10 至 2026-11-20 · 剩余 9 天',
  )
  await expect(banner.getByRole('button', { name: '调整请假' })).toBeVisible()

  // 代收标签区分来源；本人通知不带标签
  const row = (title) => page.locator('.notification-row', { hasText: title })
  await expect(row('合同到期提醒').locator('.notification-delegated')).toHaveText('请假代收 · 主办律师')
  await expect(row('合同到期提醒').locator('.notification-delegated')).toHaveClass(/source-leave/)
  await expect(row('审核待处理').locator('.notification-delegated')).toHaveText('类型代收 · 主办律师')
  await expect(row('审核待处理').locator('.notification-delegated')).toHaveClass(/source-event/)
  await expect(row('本人通知').locator('.notification-delegated')).toHaveCount(0)

  const sourceSelect = page.locator('.notification-toolbar label', { hasText: '来源' }).locator('select')
  const optionLabels = await sourceSelect.locator('option').allInnerTexts()
  expect(optionLabels).toEqual(['全部来源', '本人', '全部代收', '请假代收', '类型代收'])

  // 请假代收：只发 delegation_source=leave，不叠加 delegated
  await sourceSelect.selectOption('leave')
  await expect.poll(() => pageQueries().length).toBe(2)
  expect(pageQueries()[1].delegation_source).toBe('leave')
  expect(pageQueries()[1].delegated).toBeUndefined()
  await expect(page.locator('.notification-row')).toHaveCount(1)
  await expect(row('合同到期提醒')).toBeVisible()

  // 类型代收：delegation_source=event
  await sourceSelect.selectOption('event')
  await expect.poll(() => pageQueries().length).toBe(3)
  expect(pageQueries()[2].delegation_source).toBe('event')
  await expect(page.locator('.notification-row')).toHaveCount(1)
  await expect(row('审核待处理')).toBeVisible()

  // 本人：沿用 delegated=false
  await sourceSelect.selectOption('own')
  await expect.poll(() => pageQueries().length).toBe(4)
  expect(pageQueries()[3].delegated).toBe('false')
  expect(pageQueries()[3].delegation_source).toBeUndefined()
  await expect(page.locator('.notification-row')).toHaveCount(1)
  await expect(row('本人通知')).toBeVisible()

  // 全部代收：delegated=true，两种来源都回来
  await sourceSelect.selectOption('delegated')
  await expect.poll(() => pageQueries().length).toBe(5)
  expect(pageQueries()[4].delegated).toBe('true')
  await expect(page.locator('.notification-row')).toHaveCount(2)

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
