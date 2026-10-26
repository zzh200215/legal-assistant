import path from 'node:path'
import { test, expect } from '@playwright/test'

// System「投递看板」联调回归：/system?tab=delivery 渲染窗口统计、渠道表、死信，
// 点「重排」触发 POST /analytics/outbox/dead-letters/{kind}/{id}/retry。
// 全 mock，锁定看板字段契约与死信重排路径；真实数据口径见 docs/review/step39。
const success = (data) => ({ success: true, data })

const OVERVIEW = {
  window: { days: 7, start: '2026-10-19T00:00:00', end: '2026-10-26T00:00:00' },
  generated_at: '2026-10-26T10:00:00',
  notification: {
    total: 45,
    rate: 1,
    success: 45,
    failed: 0,
    dead_letter: 1,
    by_channel: [
      { channel: 'site', label: '站内', total: 40, delivered: 40, failed: 0, dead_letter: 0, pending: 0, rate: 1 },
      { channel: 'email', label: '邮件', total: 5, delivered: 3, failed: 1, dead_letter: 1, pending: 0, rate: 0.6 },
    ],
    by_status: { delivered: 40, sent: 3, failed: 1, dead_letter: 1 },
    backlog: { pending: 1, approved: 0, sending: 0, retry_pending: 1, stale_claims: 0, open_total: 1, oldest_pending_age_minutes: 12 },
    dead_letters: {
      total: 1,
      items: [{
        id: 77, channel: 'email', channel_label: '邮件', event_type: 'approval',
        title: '审核即将到期：合同 A', template_key: 'review_sla_due_soon', attempt: 5,
        error_code: 'TEMPLATE_ERROR', error_message: '通知模板渲染失败',
        reference_type: 'contract_review', reference_id: 1, updated_at: '2026-10-26T09:30:00',
      }],
    },
    failure_codes: [{ code: 'TEMPLATE_ERROR', count: 1 }],
  },
  email: {
    total: 2,
    rate: 0,
    sent: 0,
    failed: 2,
    dead_letter: 1,
    blocked: 0,
    rejected: 0,
    dlp_blocked: 0,
    sent_24h: 0,
    policy_enabled: true,
    backlog: { pending: 1, approved: 0, sending: 0, retry_pending: 0, stale_claims: 0, open_total: 1, oldest_pending_age_minutes: 30 },
    dead_letters: {
      total: 1,
      items: [{
        id: 9, recipient: 'b***@corp.example.com', subject: '咨询被退回', attempt: 5,
        error_code: 'SMTP_AUTH', error_message: 'SMTP 发送失败，请检查连接器与系统日志',
        dead_letter_reason: '达到最大重试次数或不可恢复错误',
        dead_letter_at: '2026-10-26T08:00:00', notification_event_id: 70,
      }],
    },
    failure_codes: [{ code: 'SMTP_AUTH', count: 1 }],
  },
  alerts: [{
    code: 'DEAD_LETTER',
    level: 'warning',
    message: '2 条投递进入死信（阈值 1），需人工重试或修复模板',
  }],
}

const TREND_DAYS = [
  { date: '2026-10-20', created: 6, delivered: 5, failed: 0, dead_letter: 0, open: 1, other: 0, rate: 1 },
  { date: '2026-10-21', created: 9, delivered: 8, failed: 1, dead_letter: 0, open: 0, other: 0, rate: 0.8889 },
  { date: '2026-10-22', created: 4, delivered: 3, failed: 0, dead_letter: 1, open: 0, other: 0, rate: 0.75 },
  { date: '2026-10-23', created: 0, delivered: 0, failed: 0, dead_letter: 0, open: 0, other: 0, rate: null },
  { date: '2026-10-24', created: 5, delivered: 5, failed: 0, dead_letter: 0, open: 0, other: 0, rate: 1 },
  { date: '2026-10-25', created: 3, delivered: 2, failed: 0, dead_letter: 0, open: 1, other: 0, rate: 1 },
  { date: '2026-10-26', created: 2, delivered: 2, failed: 0, dead_letter: 0, open: 0, other: 0, rate: 1 },
]

const HEALTH = {
  organization_id: 1,
  checks: [
    { code: 'NOTIFICATION_BACKLOG', label: '通知待投递积压', value: 1, threshold: 50, level: 'warning', hint: 'pending + approved + sending' },
    { code: 'EMAIL_BACKLOG', label: '邮件待投递积压', value: 1, threshold: 50, level: 'warning', hint: '待审批 + 待投递 + 投递中' },
    { code: 'DEAD_LETTER', label: '死信', value: 2, threshold: 1, level: 'warning', hint: '通知 1 / 邮件 1' },
    { code: 'STALE_CLAIMS', label: '卡死租约', value: 0, threshold: 1, level: 'warning', hint: 'worker 崩溃或卡死' },
    { code: 'RETRY_STUCK', label: '到期待重试', value: 1, threshold: 10, level: 'info', hint: '已过重试时间仍在等待 worker' },
  ],
  breached: [{ code: 'DEAD_LETTER', label: '死信', value: 2, threshold: 1, level: 'warning' }],
  healthy: false,
  cooldown_minutes: 180,
}

const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

// 趋势/健康在 OVERVIEW 常量之后声明（TDZ），这里补挂，避免把大段数据挪到前面。
OVERVIEW.trend = { days: 7, notification: TREND_DAYS, email: TREND_DAYS, metric: 'created_at 分桶 + 当前状态' }
OVERVIEW.health = HEALTH

test('System 投递看板与死信重排', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'system-delivery-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 3, username: 'admin', role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    // System 页其它 tab 的通用请求（只激活投递看板，其余给最小合法响应）
    if (method === 'GET' && path === '/health') return route.fulfill({ json: success({ status: 'ok', checks: { database: { status: 'ok' }, redis: { status: 'ok' }, llm_provider: { status: 'ok' } } }) })
    if (method === 'GET' && path === '/analytics/retrieval-index/stats') return route.fulfill({ json: success({ enabled: true, ttl_seconds: 300, index_ready: true, stale: false, epoch: 0, builds: 0, build_failures: 0, stale_discarded: 0, ttl_rebuilds: 0, fallback_scans: 0, index_size: 0 }) })

    if (method === 'GET' && path === '/analytics/outbox/overview') {
      requests.overviewQuery = Object.fromEntries(url.searchParams.entries())
      return route.fulfill({ json: success(OVERVIEW) })
    }
    if (method === 'POST' && path === '/analytics/outbox/dead-letters/notification/77/retry') {
      requests.retry = { kind: 'notification', id: 77 }
      return route.fulfill({ json: success({ kind: 'notification', id: 77, status: 'pending' }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/system?tab=delivery')

  // 指标卡与提示条
  await expect(page.getByText('Outbox 投递看板')).toBeVisible()
  await expect(page.getByText('通知投递率')).toBeVisible()
  await expect(page.getByText('2 条投递进入死信（阈值 1），需人工重试或修复模板')).toBeVisible()
  await expect.poll(() => requests.overviewQuery, { timeout: 5000 }).toBeTruthy()
  expect(requests.overviewQuery.days).toBe('7')

  // 趋势与健康检查
  await expect(page.getByText('投递趋势')).toBeVisible()
  await expect(page.getByText('按创建日分桶', { exact: false })).toBeVisible()
  await expect(page.getByText('投递健康')).toBeVisible()
  await expect(page.getByText('超阈值').first()).toBeVisible()
  await expect(page.getByText('冷却 180 分钟', { exact: false })).toBeVisible()

  // 渠道表：站内 / 邮件两行
  await expect(page.getByText('站内').first()).toBeVisible()
  await expect(page.getByText('邮件').first()).toBeVisible()

  // 死信表：通知 + 邮件两条
  await expect(page.getByText('审核即将到期：合同 A').first()).toBeVisible()
  await expect(page.getByText('咨询被退回').first()).toBeVisible()

  if (SHOT_DIR) {
    await page.screenshot({ path: path.join(SHOT_DIR, 'step39-delivery-dashboard.png'), fullPage: true })
  }

  // 重排第一条（通知）→ 待投递
  await page.getByRole('button', { name: '重排', exact: true }).first().click()
  await expect(page.getByText('已重排为待投递')).toBeVisible()
  await expect.poll(() => requests.retry, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.retry).toEqual({ kind: 'notification', id: 77 })

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
