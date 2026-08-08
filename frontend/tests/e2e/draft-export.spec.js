import { test, expect } from '@playwright/test'

// 文书草稿 docx 导出联调回归（修复 exportLegalDraftDocx 死代码）：
//   选文书类型 → 填必填字段 → 生成草稿 → 导出草稿 → GET /legal/drafts/{id}/export/docx
//   → blob 下载 .docx。
// 全 mock，锁定导出路径契约（此前前端只本地导出 .md，后端 docx API 从未被调用）。
const success = (data) => ({ success: true, data })

test('文书草稿：生成 → 导出 docx（走后端 API）', async ({ page }) => {
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

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'draft-export-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
    if (method === 'GET' && path === '/legal/document-templates') return route.fulfill({ json: success([{ key: 'labor_arbitration_application', label: '劳动仲裁申请书' }]) })
    if (method === 'GET' && path === '/legal/drafts') return route.fulfill({ json: success([]) })

    if (method === 'POST' && path === '/legal/drafts') {
      requests.draftCreate = request.postDataJSON()
      return route.fulfill({ json: success({ id: 1, title: '劳动仲裁申请书-联调', content: '申请人：张三\n被申请人：某公司', status: 'draft', document_type: 'labor_arbitration_application', missing_fields: [], version: 1 }) })
    }
    if (method === 'GET' && path === '/legal/drafts/1/export/docx') {
      requests.docxExport = true
      const fakeDocx = Buffer.from('PK\x03\x04fake-docx-content', 'binary')
      return route.fulfill({ body: fakeDocx, contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  // 登录 → 工作台 → 文书草稿 tab
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.getByRole('tab', { name: '文书草稿', exact: true }).click()

  // 选文书类型 → 填必填字段 → 生成草稿
  await page.locator('.el-select', { hasText: '选择文书类型' }).click()
  await page.getByRole('option', { name: '劳动仲裁申请书', exact: true }).click()
  await page.getByPlaceholder('【必填】请输入申请人').fill('张三')
  await page.getByPlaceholder('【必填】请输入被申请人').fill('某公司')
  await page.getByRole('button', { name: '生成草稿', exact: true }).click()
  await expect.poll(() => requests.draftCreate, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.draftCreate).toMatchObject({ document_type: 'labor_arbitration_application', case_id: 3 })
  await expect(page.getByText('劳动仲裁申请书-联调')).toBeVisible()

  // 导出草稿 → GET docx blob → 下载
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出草稿', exact: true }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toContain('.docx')
  expect(requests.docxExport).toBe(true)

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
