import { test, expect } from '@playwright/test'

const success = (data) => ({ success: true, data })

test('试点主链路：登录、合同审查、关键日期与门户发布', async ({ page }) => {
  const requests = {
    contractReview: null,
    deadline: null,
    portal: null,
  }
  const unexpectedApiRequests = []
  const failedResponses = []

  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      failedResponses.push(`${response.request().method()} ${response.url()} -> ${response.status()}`)
    }
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) {
      await route.continue()
      return
    }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'POST' && path === '/auth/login') {
      const body = request.postDataJSON()
      expect(body).toEqual({ username: 'pilot_lawyer', password: 'test-password' })
      await route.fulfill({ json: success({ access_token: 'pilot-e2e-token' }) })
      return
    }

    if (method === 'GET' && path === '/auth/me') {
      await route.fulfill({
        json: success({
          id: 7,
          username: 'pilot_lawyer',
          email: 'pilot@example.com',
          role: 'user',
          status: 'active',
        }),
      })
      return
    }

    // U-1 角色化引导落地页（登录后路由到 /legal-onboarding）
    if (method === 'GET' && path === '/developer/onboarding') {
      await route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
      return
    }

    // 通知铃铛首屏加载（US-002）
    if (method === 'GET' && path === '/developer/notifications/me') {
      await route.fulfill({ json: success({ items: [], unread: 0 }) })
      return
    }

    // 工作台首屏：案件列表 + 剩余配额 + 门户品牌（避免 unexpected API 500）
    if (method === 'GET' && path === '/legal/orgs/1/cases') {
      await route.fulfill({ json: success([{ id: 1, title: '试点案件', status: 'in_progress', case_type: 'labor_dispute', organization_id: 1 }]) })
      return
    }
    if (method === 'GET' && path === '/billing/subscriptions/quota') {
      await route.fulfill({
        json: success({
          consultation: { quota: 8, remaining: 8 },
          review: { quota: 8, remaining: 8 },
          draft: { quota: 8, remaining: 8 },
        }),
      })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/1/portal-branding') {
      await route.fulfill({ json: success({ portal_logo_url: '', portal_welcome_message: '' }) })
      return
    }

    if (request.headers().authorization !== 'Bearer pilot-e2e-token') {
      unexpectedApiRequests.push(`missing authorization: ${method} ${path}`)
      await route.fulfill({ status: 401, json: { detail: 'missing authorization' } })
      return
    }

    if (method === 'POST' && path === '/legal/contract-reviews') {
      requests.contractReview = request.postDataJSON()
      await route.fulfill({
        json: success({
          id: 101,
          title: requests.contractReview.title,
          content: requests.contractReview.content,
          status: 'needs_lawyer_review',
          summary: '付款与验收条款存在高风险，需要人工复核。',
          risks: [{
            label: '付款条款', clause_type: 'payment', risk_level: 'high', status: 'open',
            description: '付款节点缺少验收条件。', suggestion: '补充验收标准与付款条件。',
            source_location: { paragraph: 1 },
          }],
        }),
      })
      return
    }

    if (method === 'POST' && path === '/legal/orgs/1/cases/1/deadlines') {
      requests.deadline = request.postDataJSON()
      await route.fulfill({ json: success({ id: 201, ...requests.deadline, status: 'active' }) })
      return
    }

    if (method === 'POST' && path === '/legal/orgs/1/cases/1/portal-links') {
      requests.portal = request.postDataJSON()
      await route.fulfill({ json: success({ id: 301, token_prefix: 'pilot-otp', status: 'active' }) })
      return
    }

    if (method === 'GET' && path === '/legal/orgs/1/members') {
      await route.fulfill({ json: success([{ user_id: 7, username: '王律师' }]) })
      return
    }

    if (method === 'GET' && path === '/legal/orgs/1/cases/1/deadlines') {
      await route.fulfill({ json: success({ items: [], total: 0 }) })
      return
    }

    const emptyCollections = new Set([
      '/legal/consultations', '/legal/contract-reviews', '/legal/document-templates',
      '/legal/drafts', '/legal/review-queue', '/legal/sources',
      '/legal/orgs/1/cases/1/portal-links', '/legal/orgs/1/cases/1/progress-updates',
      '/legal/orgs/1/cases/1/members',
    ])
    if (method === 'GET' && emptyCollections.has(path)) {
      await route.fulfill({ json: success([]) })
      return
    }

    if (method === 'GET' && path === '/legal/overview') {
      await route.fulfill({ json: success({}) })
      return
    }

    if (method === 'GET' && path === '/legal/review-stats') {
      await route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
      return
    }

    unexpectedApiRequests.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled E2E API request: ${method} ${path}` } })
  })

  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot_lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  // U-1：引导落地页点「进入工作台」到达 /legal-workspace
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)

  await page.getByRole('tab', { name: '合同审查', exact: true }).click()
  await page.getByRole('textbox', { name: '合同标题' }).fill('试点技术服务合同')
  await page.getByPlaceholder('粘贴合同全文或主要条款...').fill('付款应以阶段验收完成为前提。')
  await page.getByRole('button', { name: '开始审查', exact: true }).click()
  await expect(page.getByText('付款与验收条款存在高风险，需要人工复核。')).toBeVisible()
  expect(requests.contractReview).toEqual({
    title: '试点技术服务合同',
    content: '付款应以阶段验收完成为前提。',
    case_id: 1,
  })

  await page.getByRole('tab', { name: '关键日期', exact: true }).click()
  await page.getByPlaceholder('YYYY-MM-DD').fill('2026-08-15')
  // EP 2.8+ el-select 的 placeholder 是 <span> 而非 input 属性，getByPlaceholder 匹配不到
  await page.locator('.el-select:has-text("选择负责人")').click()
  await page.getByRole('option', { name: '王律师' }).click()
  await page.getByPlaceholder('期限相关说明...').fill('提交答辩材料')
  await page.getByRole('button', { name: '创建期限', exact: true }).click()
  await expect.poll(() => requests.deadline).not.toBeNull()
  expect(requests.deadline).toMatchObject({
    deadline_type: 'hearing', deadline_at: '2026-08-15T00:00:00', owner_id: 7, description: '提交答辩材料',
  })
  expect(requests.deadline).not.toHaveProperty('type')
  expect(requests.deadline).not.toHaveProperty('deadline_date')

  await page.getByRole('tab', { name: '客户门户', exact: true }).click()
  await page.getByRole('button', { name: '创建门户链接', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建客户门户链接' })
  await dialog.getByPlaceholder('客户邮箱（用于验证码）').fill('client@example.com')
  await dialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('门户链接已创建，令牌前缀：pilot-otp')).toBeVisible()
  expect(requests.portal).toEqual({
    client_email: 'client@example.com', expires_days: 30, require_email_verification: true,
  })

  expect(unexpectedApiRequests).toEqual([])
  expect(failedResponses).toEqual([])
})
