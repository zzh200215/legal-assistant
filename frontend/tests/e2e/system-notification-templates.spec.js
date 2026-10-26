import path from 'node:path'
import { test, expect } from '@playwright/test'

// System「通知模板」联调回归：/system?tab=templates 列内置模板 → 打开编辑 → 预览（iframe）
// → 保存草稿 → 启用。全 mock，锁定 layout 覆写字段与启用链路。
const success = (data) => ({ success: true, data })

const BUILTIN = [
  {
    template_key: 'review_sla_due_soon',
    subject: '【律智检】{{title}}',
    lead: '以下审核任务即将到期，请在截止时间前完成审核。',
    action_label: '去审核',
    footnote: '若未在截止前处理，系统会在逾期后再发一次提醒。',
  },
  {
    template_key: 'review_sla_overdue',
    subject: '【律智检】{{title}}',
    lead: '以下审核任务已超过截止时间，请尽快处理。',
    action_label: '立即处理',
    footnote: '逾期记录会在审核队列中以「已逾期」标记。',
  },
]

const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

test('System 通知模板编辑与启用', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'system-templates-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 3, username: 'admin', role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/health') return route.fulfill({ json: success({ status: 'ok', checks: { database: { status: 'ok' }, redis: { status: 'ok' }, llm_provider: { status: 'ok' } } }) })
    if (method === 'GET' && path === '/analytics/retrieval-index/stats') return route.fulfill({ json: success({ enabled: true, ttl_seconds: 300, index_ready: true, stale: false, epoch: 0, builds: 0, build_failures: 0, stale_discarded: 0, ttl_rebuilds: 0, fallback_scans: 0, index_size: 0 }) })

    if (method === 'GET' && path === '/admin/notification-templates/') {
      return route.fulfill({ json: success({
        builtin: BUILTIN,
        templates: [],
        active_overrides: [],
        layout_fields: [
          { field: 'subject', max_length: 120 },
          { field: 'lead', max_length: 300 },
          { field: 'action_label', max_length: 16 },
          { field: 'footnote', max_length: 300 },
        ],
        action_url_configured: true,
      }) })
    }
    if (method === 'POST' && path === '/admin/notification-templates/preview') {
      const payload = request.postDataJSON()
      requests.preview = payload
      const lead = payload.layout?.lead || '预览导语'
      return route.fulfill({ json: success({
        body_format: payload.body_format,
        subject: payload.layout?.subject || '【律智检】预览主题',
        text: `纯文本预览：${lead}`,
        html: `<html><body><h1>预览渲染成功</h1><p>${lead}</p></body></html>`,
        action_url_configured: true,
      }) })
    }
    if (method === 'POST' && path === '/admin/notification-templates/') {
      requests.create = request.postDataJSON()
      return route.fulfill({ json: success({ id: 21, template_key: requests.create.template_key, locale: 'zh-CN', version: 1, body_format: requests.create.body_format, status: 'draft', body_template: '{}', subject_template: null, updated_at: '2026-10-27T10:00:00' }) })
    }
    if (method === 'POST' && path === '/admin/notification-templates/21/activate') {
      requests.activate = 21
      return route.fulfill({ json: success({ id: 21, template_key: 'review_sla_due_soon', locale: 'zh-CN', version: 1, body_format: 'layout', status: 'active', body_template: '{}', subject_template: null, updated_at: '2026-10-27T10:00:00' }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/system?tab=templates')

  await expect(page.getByText('通知模板', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('review_sla_due_soon').first()).toBeVisible()
  await expect(page.getByText('内置版式').first()).toBeVisible()

  // 打开编辑 → 预览 → iframe 内渲染
  await page.getByRole('button', { name: '编辑文案', exact: true }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '预览', exact: true }).click()
  await expect.poll(() => requests.preview, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.preview.body_format).toBe('layout')
  expect(requests.preview.template_key).toBe('review_sla_due_soon')
  await expect(page.frameLocator('.tpl-preview-frame').getByText('预览渲染成功')).toBeVisible()

  if (SHOT_DIR) {
    await page.screenshot({ path: path.join(SHOT_DIR, 'step40-notification-templates.png'), fullPage: false })
  }

  // 保存草稿 → 启用
  await dialog.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect.poll(() => requests.create, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.create.layout.lead).toContain('审核任务即将到期')
  await expect(page.getByText('草稿已保存', { exact: false })).toBeVisible()
  await dialog.getByRole('button', { name: '启用该版本', exact: true }).click()
  await expect.poll(() => requests.activate, { timeout: 5000 }).toBe(21)
  await expect(page.getByText('已启用，新的通知将使用该文案', { exact: false })).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
