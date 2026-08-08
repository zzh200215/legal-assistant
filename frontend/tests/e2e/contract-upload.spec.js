import { test, expect } from '@playwright/test'

// 合同审查文件上传联调回归：el-upload before-upload → multipart POST
// /legal/contract-reviews/upload（file/title/case_id）→ 返回审查结果渲染。
// 全 mock，锁定上传路径契约（区别于 U-3 的纯文本提交）。
const success = (data) => ({ success: true, data })

test('合同审查：上传文件触发 multipart 审查并渲染结果', async ({ page }) => {
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

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'review-upload-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })

    if (method === 'POST' && path === '/legal/contract-reviews/upload') {
      requests.upload = request.postDataBuffer().toString('utf8')
      return route.fulfill({ json: success({ id: 1, title: '联调合同.txt', status: 'draft', summary: '上传合同审查完成，共识别 3 处风险点', risks: [], version: 1 }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  // 登录 → 工作台 → 合同审查 tab
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.getByRole('tab', { name: '合同审查', exact: true }).click()

  // 上传文件 → multipart POST
  await page.locator('#pane-contract input[type="file"]').setInputFiles({
    name: '联调合同.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('甲乙双方就技术服务事宜达成如下协议，本合同自双方签署之日起生效。', 'utf8'),
  })

  // 结果渲染 + 消息 + multipart 载荷含 file/case_id
  await expect(page.getByText('上传合同审查完成，共识别 3 处风险点')).toBeVisible()
  await expect(page.getByText('合同文件审查完成')).toBeVisible()
  expect(requests.upload).toBeDefined()
  expect(requests.upload).toContain('联调合同.txt')
  expect(requests.upload).toContain('name="case_id"')

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
