import { test, expect } from '@playwright/test'

// 通知偏好设置页：事件类型切换、已有偏好回显、保存下推、静默校验；
// 以及用户级请假代理（全部事件）的设置、回显与结束。
// 全 mock，锁定 API 契约与表单接线。
const success = (data) => ({ success: true, data })
const ME = { id: 15, username: 'pilot01-lawyer', role: 'dept_admin', organization_id: 5, department_id: 51, status: 'active' }
const MEMBERS = [
  { user_id: 15, username: 'pilot01-lawyer', full_name: '主办律师', legal_role: 'admin' },
  { user_id: 16, username: 'lawyer2', full_name: '协办律师', legal_role: 'editor' },
]
const deadlinePref = {
  id: 1,
  organization_id: 5,
  event_type: 'deadline',
  channels_json: '["site"]',
  mute_start: '22:00',
  mute_end: '07:00',
  timezone: 'Asia/Tokyo',
  delegate_user_id: 16,
  delegate_start: '2026-11-10',
  delegate_end: '2026-11-20',
  summary_frequency: 'daily',
}
const NO_LEAVE = {
  delegate_user_id: null,
  delegate_name: null,
  leave_start: null,
  leave_end: null,
  note: null,
  timezone: 'Asia/Shanghai',
  status: 'none',
  days_left: null,
  updated_at: null,
}

test('通知偏好：事件类型切换→回显→保存→静默校验', async ({ page }) => {
  const puts = []
  const failed = []
  let leave = NO_LEAVE
  const leavePuts = []
  let leaveDeletes = 0
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'pref-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/orgs/5/members') return route.fulfill({ json: success(MEMBERS) })
    if (method === 'GET' && path === '/developer/notification-leave/me') {
      return route.fulfill({ json: success(leave) })
    }
    if (method === 'PUT' && path === '/developer/notification-leave/me') {
      const body = route.request().postDataJSON()
      leavePuts.push(body)
      const member = MEMBERS.find((item) => item.user_id === body.delegate_user_id)
      leave = {
        ...body,
        delegate_name: member ? member.full_name : null,
        timezone: 'Asia/Shanghai',
        status: 'active',
        days_left: 9,
        updated_at: '2026-11-11T09:00:00',
      }
      return route.fulfill({ json: success(leave) })
    }
    if (method === 'DELETE' && path === '/developer/notification-leave/me') {
      leaveDeletes += 1
      leave = NO_LEAVE
      return route.fulfill({ json: success(leave) })
    }
    if (method === 'GET' && path === '/developer/notification-preferences/me') {
      return route.fulfill({ json: success([deadlinePref]) })
    }
    if (method === 'PUT' && path === '/developer/notification-preferences/me') {
      const body = route.request().postDataJSON()
      const eventType = url.searchParams.get('event_type')
      puts.push({ eventType, body })
      return route.fulfill({
        json: success({ id: 1, organization_id: 5, event_type: eventType, ...body }),
      })
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/settings/notifications')
  await expect(page.getByRole('heading', { name: '通知偏好' })).toBeVisible()

  // 请假代理：默认未设置 → 设置 → 请假中 → 结束请假
  const leavePill = page.locator('.leave-pill')
  await expect(leavePill).toHaveText('未设置')
  await expect(page.locator('.leave-summary')).toContainText('未设置请假代理')

  await page.locator('.pref-field', { hasText: '请假代理' }).locator('select').selectOption('16')
  await page.locator('.pref-field', { hasText: '请假开始' }).locator('input').fill('2026-11-10')
  await page.locator('.pref-field', { hasText: '请假结束' }).locator('input').fill('2026-11-20')
  await page.locator('.pref-field', { hasText: '备注' }).locator('input').fill('出差')
  await page.getByRole('button', { name: '保存请假' }).click()

  await expect(leavePill).toHaveText('请假中')
  await expect(page.locator('.leave-summary')).toContainText(
    '代 协办律师 接收 · 2026-11-10 至 2026-11-20 · 剩余 9 天',
  )
  expect(leavePuts).toEqual([
    { delegate_user_id: 16, leave_start: '2026-11-10', leave_end: '2026-11-20', note: '出差' },
  ])

  await page.getByRole('button', { name: '结束请假' }).click()
  await expect(leavePill).toHaveText('未设置')
  expect(leaveDeletes).toBe(1)

  await expect(page.locator('.pref-nav-item', { hasText: '关键日期' })).toContainText('已配置')

  // 切换到关键日期 → 回显已有偏好（邮件未勾选、静默/时区）
  await page.locator('.pref-nav-item', { hasText: '关键日期' }).click()
  const emailCheck = page.locator('.pref-check', { hasText: '邮件' }).locator('input')
  await expect(emailCheck).not.toBeChecked()
  await expect(page.locator('.pref-field', { hasText: '静默开始' }).locator('input')).toHaveValue('22:00')
  await expect(page.locator('.pref-field', { hasText: '静默结束' }).locator('input')).toHaveValue('07:00')
  await expect(page.locator('.pref-field', { hasText: '时区' }).locator('select')).toHaveValue('Asia/Tokyo')
  await expect(page.locator('.pref-field', { hasText: '代理接收人' }).locator('select')).toHaveValue('16')
  await expect(page.locator('.pref-field', { hasText: '代理开始' }).locator('input')).toHaveValue('2026-11-10')
  await expect(page.locator('.pref-field', { hasText: '代理结束' }).locator('input')).toHaveValue('2026-11-20')

  // 修改并保存（代理改为 15，结束日期延长一天）
  await emailCheck.check()
  await page.locator('.pref-field', { hasText: '静默结束' }).locator('input').fill('06:30')
  await page.locator('.pref-field', { hasText: '代理接收人' }).locator('select').selectOption('15')
  await page.locator('.pref-field', { hasText: '代理结束' }).locator('input').fill('2026-11-21')
  await page.getByRole('button', { name: '保存偏好' }).click()

  await expect(page.locator('.pref-saved')).toHaveText('已保存')
  expect(puts).toHaveLength(1)
  expect(puts[0].eventType).toBe('deadline')
  expect(JSON.parse(puts[0].body.channels_json)).toEqual(['site', 'email'])
  // 页面未暴露的摘要字段按原值保留
  expect(puts[0].body).toMatchObject({
    mute_start: '22:00',
    mute_end: '06:30',
    timezone: 'Asia/Tokyo',
    delegate_user_id: 15,
    delegate_start: '2026-11-10',
    delegate_end: '2026-11-21',
    summary_frequency: 'daily',
  })

  // 切换到默认事件 → 默认站内 + 邮件；只填静默开始应被前端拦住
  await page.locator('.pref-nav-item', { hasText: '默认（全部事件）' }).click()
  await expect(emailCheck).toBeChecked()
  await page.locator('.pref-field', { hasText: '静默开始' }).locator('input').fill('21:00')
  await page.getByRole('button', { name: '保存偏好' }).click()
  await expect(page.getByText('请同时填写静默开始和结束时间')).toBeVisible()
  expect(puts).toHaveLength(1)

  // 补齐结束时间后保存（event_type=all，channels 默认站内+邮件）
  await page.locator('.pref-field', { hasText: '静默结束' }).locator('input').fill('08:00')
  await page.getByRole('button', { name: '保存偏好' }).click()
  await expect.poll(() => puts.length).toBe(2)
  expect(puts[1].eventType).toBe('all')
  expect(JSON.parse(puts[1].body.channels_json)).toEqual(['site', 'email'])
  expect(puts[1].body).toMatchObject({ mute_start: '21:00', mute_end: '08:00' })

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
