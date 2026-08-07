import { test, expect } from '@playwright/test'

// 客户门户（/portal/c/:token）流程回归：
//   OTP 表单 → 掩码邮箱（后端字段 email_masked，曾误读 masked_email 导致不显示）
//   → 验证 → 内容（进度更新）→ 反馈。
// 全 mock（公开路由，无需登录），锁定字段契约与 UI 接线。
const success = (data) => ({ success: true, data })
const TOKEN = 'e2e-portal-token'

const CONTENT = {
  link_id: 1,
  case_id: 3,
  progress_updates: [{
    id: 3, title: '联调门户进度', body: '案件进入庭审准备阶段。',
    next_steps: '下周提交答辩材料', status: 'published', published_at: '2026-08-07T10:00:00',
  }],
  documents: [],
  invoice: null,
  organization: { name: '试点律所01', portal_logo_url: null, portal_welcome_message: null },
}

test('客户门户：发送验证码→掩码邮箱→验证→查看进度→反馈', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []

  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()

    if (method === 'POST' && path === `/legal/portal/${TOKEN}/send-otp`) {
      requests.sendOtp = true
      // 后端实际返回 email_masked（曾因前端误读 masked_email 而不显示）
      await route.fulfill({ json: success({ sent: true, email_masked: 'por***@example.com', ttl_seconds: 300 }) })
      return
    }
    if (method === 'POST' && path === `/legal/portal/${TOKEN}/verify`) {
      expect(new URL(route.request().url()).searchParams.get('otp')).toBe('123456')
      requests.verify = true
      await route.fulfill({ json: success({ session_token: 'e2e-session' }) })
      return
    }
    if (method === 'GET' && path === `/legal/portal/${TOKEN}/content`) {
      const sess = route.request().headers()['x-portal-session']
      requests.content = sess
      await route.fulfill({ json: success(CONTENT) })
      return
    }
    if (method === 'POST' && path === `/legal/portal/${TOKEN}/feedback`) {
      requests.feedback = route.request().postDataJSON()
      expect(route.request().headers()['x-portal-session']).toBe('e2e-session')
      await route.fulfill({ json: success({ ok: true }) })
      return
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto(`/portal/c/${TOKEN}`)

  // OTP 表单 + 掩码邮箱显示（修复断言）
  await expect(page.getByPlaceholder('请输入验证码')).toBeVisible()
  await expect(page.getByText('验证码已发送至 por***@example.com')).toBeVisible()
  expect(requests.sendOtp).toBe(true)

  // 验证 → 内容
  await page.getByPlaceholder('请输入验证码').fill('123456')
  await page.getByRole('button', { name: /验证|进入/ }).first().click()
  await expect(page.getByText('联调门户进度')).toBeVisible()
  await expect(page.getByText('案件进入庭审准备阶段。')).toBeVisible()
  await expect(page.getByText('试点律所01').first()).toBeVisible()
  expect(requests.verify).toBe(true)
  expect(requests.content).toBe('e2e-session')

  // 反馈
  await page.getByRole('button', { name: '有帮助' }).click()
  await expect(page.getByText('已收到反馈：有帮助')).toBeVisible()
  expect(requests.feedback).toEqual({ score: 1, note: null })

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
