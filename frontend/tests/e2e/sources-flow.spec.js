import { test, expect } from '@playwright/test'

// 法源管理 tab（LegalWorkspace sources）联调回归：
//   列表加载（挂载即加载，非 lazy tab）→ 检索测试 POST → 新建法源 POST →
//   状态切换 PATCH → 编辑 PUT → 删除 DELETE（确认框）。
// 全 mock，锁定法源 CRUD 契约与 tab 接线。
const success = (data) => ({ success: true, data })

let SOURCES = [{
  id: 1, title: '民法典合同编示范条款', source_type: 'statute', citation: '民法典第470条', jurisdiction: '中国大陆',
  version: 'v1', status: 'active', content: '示范条款内容', document_number: '', promulgator: '', full_text: '',
  law_areas: ['contract'], keywords: ['合同', '条款'], created_at: '2026-08-05T09:00:00', updated_at: '2026-08-05T09:00:00',
}]

test('法源管理：列表→检索测试→新建→状态→编辑→删除', async ({ page }) => {
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

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'sources-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })

    // 法源
    if (method === 'GET' && path === '/legal/sources') return route.fulfill({ json: success(SOURCES) })
    if (method === 'POST' && path === '/legal/sources') {
      requests.sourceCreate = request.postDataJSON()
      SOURCES = [{ ...SOURCES[0], id: 2, title: requests.sourceCreate.title, content: requests.sourceCreate.content }, ...SOURCES]
      return route.fulfill({ json: success(SOURCES[0]) })
    }
    if (method === 'PUT' && /^\/legal\/sources\/\d+$/.test(path)) {
      requests.sourceUpdate = request.postDataJSON()
      SOURCES = SOURCES.map((s) => (s.id === Number(path.split('/')[3]) ? { ...s, title: requests.sourceUpdate.title } : s))
      return route.fulfill({ json: success(SOURCES[0]) })
    }
    if (method === 'PATCH' && /^\/legal\/sources\/\d+\/status$/.test(path)) {
      requests.sourceStatus = request.postDataJSON()
      SOURCES = SOURCES.map((s) => (s.id === Number(path.split('/')[3]) ? { ...s, status: requests.sourceStatus.status } : s))
      return route.fulfill({ json: success({ id: Number(path.split('/')[3]), status: requests.sourceStatus.status, updated_at: '2026-08-07T10:00:00', graph_synced: true }) })
    }
    if (method === 'DELETE' && /^\/legal\/sources\/\d+$/.test(path)) {
      requests.sourceDelete = true
      SOURCES = []
      return route.fulfill({ json: success({ ok: true }) })
    }
    if (method === 'POST' && path === '/legal/sources/retrieval-test') {
      requests.retrieval = request.postDataJSON()
      return route.fulfill({ json: success({
        question: requests.retrieval.question,
        total_sources: 1,
        results: [{ title: '民法典合同编示范条款', total_score: 8, score_breakdown: { citation_match: 1, keyword_match: 3, category_match: 2, query_coverage: 1, status_weight: 1 }, status: 'active', matched_keywords: ['合同'] }],
      }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.getByRole('tab', { name: '法源管理', exact: true }).click()

  // 列表（挂载即加载）渲染
  await expect(page.getByText('民法典合同编示范条款').first()).toBeVisible()
  await expect(page.getByText('民法典第470条').first()).toBeVisible()

  // 检索测试 → POST + 结果渲染
  await page.getByPlaceholder('输入问题').fill('合同解除的条件是什么')
  await page.getByRole('button', { name: '测试召回', exact: true }).click()
  await expect(page.getByText('共 1 条法源，召回 1 条有效匹配')).toBeVisible()
  expect(requests.retrieval).toEqual({ question: '合同解除的条件是什么' })

  // 新建法源 → POST（pydantic 忽略 keywordsInput 等额外字段，按 matchObject 断言）
  await page.getByRole('button', { name: '新建法源', exact: true }).click()
  const dialog = page.locator('.el-dialog:visible')
  await dialog.getByPlaceholder('《劳动合同法》').fill('新法源-解除劳动合同')
  await dialog.getByPlaceholder('法源核心内容摘要').fill('解除劳动合同的法定情形与程序')
  await dialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => requests.sourceCreate, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.sourceCreate).toMatchObject({ title: '新法源-解除劳动合同', content: '解除劳动合同的法定情形与程序', source_type: 'statute', status: 'active', jurisdiction: '中国大陆' })
  await expect(page.getByText('新法源-解除劳动合同')).toBeVisible()

  // 状态切换 → PATCH {status}
  await page.locator('.el-table__row', { hasText: '民法典合同编示范条款' }).locator('.el-select').click()
  await page.getByRole('option', { name: '已失效', exact: true }).click()
  await expect.poll(() => requests.sourceStatus, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.sourceStatus).toEqual({ status: 'inactive' })

  // 编辑（旧法源行）→ PUT
  await page.locator('.el-table__row', { hasText: '民法典合同编示范条款' }).getByRole('button', { name: '编辑', exact: true }).click()
  const editDialog = page.locator('.el-dialog:visible')
  await editDialog.getByPlaceholder('《劳动合同法》').fill('民法典合同编（修订）')
  await editDialog.getByRole('button', { name: '保存', exact: true }).click()
  await expect.poll(() => requests.sourceUpdate, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.sourceUpdate).toMatchObject({ title: '民法典合同编（修订）', content: '示范条款内容' })

  // 删除（编辑后的行）→ 确认框 → DELETE
  await page.locator('.el-table__row', { hasText: '民法典合同编（修订）' }).getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('button', { name: '确认删除', exact: true }).click()
  await expect.poll(() => requests.sourceDelete, { timeout: 5000 }).not.toBeUndefined()
  await expect(page.getByText('法源已删除')).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
