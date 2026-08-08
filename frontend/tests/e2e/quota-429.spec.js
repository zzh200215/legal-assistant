import { test, expect } from '@playwright/test'

// 配额耗尽与 429 联调回归：
//   1. 配额耗尽 → 咨询提交返回 429 QUOTA_EXCEEDED → ElMessage 透出后端 detail
//   2. 配额用尽（remaining=0，本消耗后最后一次）→ 结果卡显示 danger tag「升级解锁更多」
//   3. 配额有剩余 → 结果卡显示 warning tag「本月剩余 N/M」
// 全 mock，锁定配额提示与 429 错误路径接线。
const success = (data) => ({ success: true, data })

const QUOTA = (consultation) => ({
  year_month: '2026-08', plan_tier: 'free',
  consultation: { used: 8 - (consultation.remaining ?? 8), quota: consultation.quota ?? 8, unlimited: false, remaining: consultation.remaining ?? 8 },
  review: { used: 0, quota: 2, unlimited: false, remaining: 2 },
  draft: { used: 0, quota: 2, unlimited: false, remaining: 2 },
})

const CONSULT_RESULT = {
  id: 1, category: 'labor_dispute', risk_level: 'high', confidence: 85,
  known_facts: ['在公司工作3年'], missing_facts: [], status: 'pending_review',
  references: [{ source_id: 1, title: '劳动合同法（参考）' }],
  advice: '建议优先协商解除并结清经济补偿。', feedback_score: null,
}

async function setup(page, { quota, consultStatus }) {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'quota-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success(QUOTA(quota)) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
    if (method === 'POST' && path === '/legal/consultations') {
      requests.consult = request.postDataJSON()
      if (consultStatus === 429) {
        return route.fulfill({ status: 429, json: { success: false, message: '本月咨询配额已用完，请升级订阅', data: null, error: { code: 'QUOTA_EXCEEDED', detail: '本月咨询配额已用完，请升级订阅' }, detail: '本月咨询配额已用完，请升级订阅' } })
      }
      return route.fulfill({ json: success(CONSULT_RESULT) })
    }
    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })
  return { requests, unexpected, failed }
}

async function loginToWorkspace(page) {
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
}

test('配额耗尽 429：咨询提交被拒并透出升级提示', async ({ page }) => {
  const { unexpected, failed } = await setup(page, { quota: { remaining: 0 }, consultStatus: 429 })
  await loginToWorkspace(page)

  await page.getByPlaceholder('例如：我在公司工作了3年').fill('被辞退未获经济补偿')
  await page.getByRole('button', { name: '提交咨询', exact: true }).click()
  await expect(page.getByText('本月咨询配额已用完，请升级订阅')).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed.filter((f) => !f.includes('-> 429')).length).toBe(0)
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('配额用尽（remaining=0）：结果卡显示红色升级引导', async ({ page }) => {
  const { unexpected, failed } = await setup(page, { quota: { remaining: 0 }, consultStatus: 200 })
  await loginToWorkspace(page)

  await page.getByPlaceholder('例如：我在公司工作了3年').fill('被辞退未获经济补偿')
  await page.getByRole('button', { name: '提交咨询', exact: true }).click()
  await expect(page.getByText('本月咨询额度已用尽，升级解锁更多')).toBeVisible()
  await expect(page.getByText('建议优先协商解除并结清经济补偿。')).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('配额有剩余：结果卡显示剩余额度 warning 提示', async ({ page }) => {
  const { unexpected, failed } = await setup(page, { quota: { remaining: 5, quota: 8 }, consultStatus: 200 })
  await loginToWorkspace(page)

  await page.getByPlaceholder('例如：我在公司工作了3年').fill('被辞退未获经济补偿')
  await page.getByRole('button', { name: '提交咨询', exact: true }).click()
  await expect(page.getByText('本月咨询剩余 5/8')).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
