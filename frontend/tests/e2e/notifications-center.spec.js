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
    if (method === 'GET' && path === '/developer/notifications/delegated-summary') {
      return route.fulfill({ json: success({ items: [], total: 0 }) })
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

test('通知中心：我代收了谁 → 点分组筛到该同事 → 清除', async ({ page }) => {
  const listQueries = []
  const summaryQueries = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'center-proxy-token'))

  const NO_LEAVE = {
    delegate_user_id: null, delegate_name: null, leave_start: null, leave_end: null,
    note: null, timezone: 'Asia/Shanghai', status: 'none', days_left: null, updated_at: null,
  }
  const SUMMARY = [
    { user_id: 15, name: '主办律师', count: 2, unread: 1, last_at: '2026-11-14T09:00:00' },
    { user_id: 16, name: '协办律师', count: 1, unread: 1, last_at: '2026-11-12T09:00:00' },
  ]
  const PROXY_ITEMS = [
    { id: 11, title: '合同到期提醒', event_type: 'deadline', status: 'delivered', created_at: '2026-11-14T09:00:00', on_behalf_of_user_id: 15, on_behalf_of_source: 'leave', on_behalf_of_name: '主办律师' },
    { id: 12, title: '审核待处理', event_type: 'approval', status: 'read', created_at: '2026-11-13T09:00:00', on_behalf_of_user_id: 15, on_behalf_of_source: 'event', on_behalf_of_name: '主办律师' },
    { id: 13, title: '任务到期提醒', event_type: 'workflow', status: 'delivered', created_at: '2026-11-12T09:00:00', on_behalf_of_user_id: 16, on_behalf_of_source: 'leave', on_behalf_of_name: '协办律师' },
    { id: 14, title: '本人通知', event_type: 'deadline', status: 'delivered', created_at: '2026-11-11T09:00:00', on_behalf_of_user_id: null, on_behalf_of_source: null, on_behalf_of_name: null },
  ]

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notification-leave/me') {
      return route.fulfill({ json: success(NO_LEAVE) })
    }
    if (method === 'GET' && path === '/developer/notifications/delegated-summary') {
      summaryQueries.push(Object.fromEntries(url.searchParams))
      const days = url.searchParams.get('days')
      // 近 7 天只剩一位同事的两条，用来验证时间窗同时作用到列表与面板
      const items = days === '7' ? [SUMMARY[0]] : SUMMARY
      return route.fulfill({ json: success({ items, total: items.length }) })
    }
    if (method === 'GET' && path === '/developer/notifications/me') {
      listQueries.push(Object.fromEntries(url.searchParams))
      const origin = url.searchParams.get('delegated_from_user_id')
      const items = PROXY_ITEMS.filter((item) => (
        origin ? String(item.on_behalf_of_user_id) === origin : true
      ))
      return route.fulfill({
        json: success({ items, unread: items.length, total: items.length, page: 1, page_size: 20 }),
      })
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/notifications')
  await expect(page.getByRole('heading', { name: '通知中心' })).toBeVisible()

  // 分组面板：人数与总条数、每行条数/未读/最近时间
  const proxy = page.locator('.notification-proxy')
  await expect(proxy).toBeVisible()
  await expect(proxy.locator('.notification-proxy-head')).toContainText('我代收的对象')
  await expect(proxy.locator('.notification-proxy-head')).toContainText('2 人 · 共 3 条')
  const aliceRow = proxy.locator('.notification-proxy-row', { hasText: '主办律师' })
  await expect(aliceRow).toContainText('代收 2 条 · 未读 1')
  await expect(aliceRow).toContainText('2026-11-14 09:00')
  const bobRow = proxy.locator('.notification-proxy-row', { hasText: '协办律师' })
  await expect(bobRow).toContainText('代收 1 条 · 未读 1')

  // 点分组：只筛这位同事，出现可清除的筛选条
  await aliceRow.click()
  // 顶部铃铛也请求同一个接口（不带分页参数），只看通知中心自己的请求
  const pageQueries = () => listQueries.filter((query) => query.page_size === '20')
  await expect.poll(() => pageQueries().length).toBe(2)
  expect(pageQueries()[1].delegated_from_user_id).toBe('15')
  await expect(page.locator('.notification-proxy-active')).toContainText('正在看 主办律师 的代收')
  await expect(aliceRow).toHaveClass(/active/)
  await expect(page.locator('.notification-row')).toHaveCount(2)
  await expect(page.locator('.notification-row', { hasText: '任务到期提醒' })).toHaveCount(0)

  // 清除：参数消失，列表恢复
  await page.locator('.notification-proxy-active').getByRole('button', { name: '返回全部' }).click()
  await expect.poll(() => pageQueries().length).toBe(3)
  expect(pageQueries()[2].delegated_from_user_id).toBeUndefined()
  await expect(page.locator('.notification-proxy-active')).toHaveCount(0)
  await expect(page.locator('.notification-row')).toHaveCount(4)

  // 时间窗：列表与汇总同时带 days，面板标题写明窗口
  const daysSelect = page.locator('.notification-toolbar label', { hasText: '时间' }).locator('select')
  await daysSelect.selectOption('7')
  await expect.poll(() => pageQueries().length).toBe(4)
  expect(pageQueries()[3].days).toBe('7')
  await expect.poll(() => summaryQueries.at(-1)?.days).toBe('7')
  await expect(proxy.locator('.notification-proxy-head')).toContainText('近 7 天 · 1 人 · 共 2 条')

  await daysSelect.selectOption('')
  await expect.poll(() => pageQueries().length).toBe(5)
  expect(pageQueries()[4].days).toBeUndefined()
  await expect.poll(() => summaryQueries.at(-1)?.days).toBeUndefined()
  await expect(proxy.locator('.notification-proxy-head')).toContainText('全部时间 · 2 人 · 共 3 条')

  // 明细：先加一个状态筛选，点「明细」后其它筛选被清空，只留该同事
  const statusSelect = page.locator('.notification-toolbar label', { hasText: '状态' }).locator('select')
  await statusSelect.selectOption('unread')
  await expect.poll(() => pageQueries().length).toBe(6)
  expect(pageQueries()[5].status).toBe('unread')

  await page.locator('.notification-proxy-row', { hasText: '协办律师' })
    .getByRole('button', { name: '明细' }).click()
  await expect.poll(() => pageQueries().length).toBe(7)
  const detail = pageQueries()[6]
  expect(detail.delegated_from_user_id).toBe('16')
  expect(detail.status).toBe('all')
  await expect(page.locator('.notification-proxy-active')).toContainText('正在看 协办律师 的代收，共 1 条')

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
