import { test, expect } from '@playwright/test'

// 合同台账 tab（LegalContracts）联调回归：
//   列表加载（org+case 过滤）→ 新建合同 POST → 版本列表/保存新版本 POST →
//   版本 Diff GET（base/target 参数）→ 关键节点 → 签署在 signing_enabled=false 时禁用。
// 全 mock，锁定 API 契约与懒加载 tab 挂载接线。
const success = (data) => ({ success: true, data })

const CONTRACTS = [
  { id: 1, contract_no: 'HT-2026-001', title: '劳动合同模板联调', counterparty: '某科技有限公司', status: 'active', organization_id: 5, case_id: 3, updated_at: '2026-08-07T10:00:00' },
  { id: 2, contract_no: 'HT-2026-002', title: '采购协议联调', counterparty: '某供应链公司', status: 'draft', organization_id: 5, case_id: 3, updated_at: '2026-08-06T10:00:00' },
]
let VERSIONS = [
  { id: 11, contract_id: 1, organization_id: 5, version_no: 1, source_type: 'text_snapshot', parse_status: 'ready', version_note: '首版', created_at: '2026-08-05T09:00:00' },
  { id: 12, contract_id: 1, organization_id: 5, version_no: 2, source_type: 'text_snapshot', parse_status: 'ready', version_note: '修订版', created_at: '2026-08-06T09:00:00' },
]

test('合同台账：列表→新建→版本→Diff→关键节点→签署禁用', async ({ page }) => {
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

    if (method === 'POST' && path === '/auth/login') return route.fulfill({ json: success({ access_token: 'contracts-e2e-token' }) })
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active', organization_id: 5 }) })
    if (method === 'GET' && path === '/developer/onboarding') return route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })

    // 合同台账 tab
    if (method === 'GET' && path === '/legal/features') return route.fulfill({ json: success({ signing_enabled: false }) })
    if (method === 'GET' && path === '/legal/orgs/5/contracts') {
      requests.listContracts = url.searchParams.get('case_id')
      return route.fulfill({ json: success({ items: CONTRACTS, total: 2, page: 1, page_size: 20 }) })
    }
    if (method === 'POST' && path === '/legal/orgs/5/contracts') {
      requests.createContract = request.postDataJSON()
      return route.fulfill({ json: success({ id: 3, contract_no: 'HT-2026-003', title: requests.createContract.title, status: 'draft' }) })
    }
    if (method === 'GET' && path === '/legal/contracts/1/versions') return route.fulfill({ json: success(VERSIONS) })
    if (method === 'POST' && path === '/legal/contracts/1/versions') {
      requests.createVersion = request.postDataJSON()
      VERSIONS = [{ id: 13, contract_id: 1, organization_id: 5, version_no: 3, source_type: 'text_snapshot', parse_status: 'uploading', version_note: '工作台新增版本', created_at: '2026-08-07T11:00:00' }, ...VERSIONS]
      return route.fulfill({ json: success(VERSIONS[0]) })
    }
    if (method === 'GET' && path === '/legal/contracts/1/diff') {
      requests.diff = { base: url.searchParams.get('base_version'), target: url.searchParams.get('target_version') }
      return route.fulfill({ json: success({ changes: [{ field: '甲方名称', base: 'A公司', target: 'B公司' }] }) })
    }
    if (method === 'GET' && path === '/legal/contracts/1/milestones') return route.fulfill({ json: success([{ id: 1, contract_id: 1, milestone_type: 'expiry', standard_date: '2026-12-31', status: 'confirmed' }]) })

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  // 登录 → 工作台 → 合同台账 tab
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot01-lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)
  await page.getByRole('tab', { name: '合同台账', exact: true }).click()

  // 列表（带 case 过滤）+ 签署禁用（特性开关 false）
  await expect(page.getByText('劳动合同模板联调')).toBeVisible()
  await expect(page.getByText('采购协议联调')).toBeVisible()
  expect(requests.listContracts).toBe('3')
  const signBtn = page.getByRole('button', { name: '签署', exact: true }).first()
  await expect(signBtn).toBeDisabled()

  // 新建合同 → POST 载荷（创建弹窗 input 无 placeholder，按 form-item 标签定位）
  await page.getByRole('button', { name: '新建合同', exact: true }).click()
  const createDialog = page.locator('.el-dialog:visible')
  await createDialog.locator('.el-form-item', { hasText: '合同名称' }).locator('input').fill('补充协议联调')
  await createDialog.locator('.el-form-item', { hasText: '相对方' }).locator('input').fill('某客户有限公司')
  await createDialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect.poll(() => requests.createContract).not.toBeNull()
  expect(requests.createContract).toEqual({ title: '补充协议联调', counterparty: '某客户有限公司', contract_type: '', contract_no: null, case_id: 3 })

  // 版本：列表 + 保存新版本 POST
  await page.getByRole('button', { name: '版本', exact: true }).first().click()
  const versionsDialog = page.locator('.el-dialog:visible')
  await expect(versionsDialog.getByText('首版')).toBeVisible()
  await versionsDialog.getByPlaceholder('粘贴本次合同文本快照').fill('第三条 甲方应于签署后 30 日内支付首期款。')
  await versionsDialog.getByRole('button', { name: '保存新版本', exact: true }).click()
  await expect.poll(() => requests.createVersion).not.toBeNull()
  expect(requests.createVersion).toEqual({ source_type: 'text_snapshot', text_snapshot: '第三条 甲方应于签署后 30 日内支付首期款。', version_note: '工作台新增版本' })
  await expect(versionsDialog.getByText('工作台新增版本').first()).toBeVisible()

  // Diff：选版本 → 对比 → GET 参数（ESC 关闭版本对话框）
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Diff', exact: true }).first().click()
  const diffDialog = page.locator('.el-dialog:visible')
  await diffDialog.locator('.el-select', { hasText: '基准版本' }).click()
  await page.getByRole('option', { name: 'V1', exact: true }).click()
  await page.waitForTimeout(400) // 等基准下拉关闭动画结束，避免残留 popper 与目标下拉同时"可见"
  await diffDialog.locator('.el-select', { hasText: '目标版本' }).click()
  await page.getByRole('option', { name: 'V2', exact: true }).click()
  await diffDialog.getByRole('button', { name: '对比', exact: true }).click()
  await expect(diffDialog.getByText('甲方名称')).toBeVisible()
  expect(requests.diff).toEqual({ base: '1', target: '2' })
  await page.keyboard.press('Escape')

  // 关键节点
  await page.getByRole('button', { name: '关键节点', exact: true }).first().click()
  const msDialog = page.locator('.el-dialog:visible')
  await expect(msDialog.getByText('2026-12-31')).toBeVisible()

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
