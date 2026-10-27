import path from 'node:path'
import { test, expect } from '@playwright/test'

// 合同台账 tab（LegalContracts）联调回归：
//   列表加载（org+case 过滤）→ 审查/风险筛选与排序参数 → 新建合同 POST → 版本列表/保存新版本 POST →
//   版本 Diff GET（base/target 参数）→ 关键节点 → 签署在 signing_enabled=false 时禁用。
// 全 mock，锁定 API 契约与懒加载 tab 挂载接线。
const success = (data) => ({ success: true, data })
const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

// 台账列较多（含审查时间与严重度分布），截图文案需要更宽的视口才能同时看到筛选与右侧列。
test.use({ viewport: { width: 1600, height: 900 } })

const CONTRACTS = [
  { id: 1, contract_no: 'HT-2026-001', title: '劳动合同模板联调', counterparty: '某科技有限公司', status: 'active', organization_id: 5, case_id: 3, updated_at: '2026-08-07T10:00:00',
    review: { id: 71, status: 'needs_lawyer_review', version: 2, risks_total: 5, risks_high: 2, risks_critical: 1, risks_medium: 2, risks_low: 1, risks_unanchored: 2, reviewed_at: '2026-08-07T09:30:00' } },
  { id: 2, contract_no: 'HT-2026-002', title: '采购协议联调', counterparty: '某供应链公司', status: 'draft', organization_id: 5, case_id: 3, updated_at: '2026-08-06T10:00:00', review: null },
  { id: 3, contract_no: 'HT-2026-003', title: '补充协议联调', counterparty: '某物流公司', status: 'draft', organization_id: 5, case_id: 3, updated_at: '2026-08-05T10:00:00',
    review: { id: 72, status: 'pending_review', version: 1, risks_total: 2, risks_high: 0, risks_critical: 0, risks_medium: 1, risks_low: 1, risks_unanchored: 1, reviewed_at: '2026-08-05T15:00:00' } },
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
    // 案件工作区新增的读取（api/auth.js:9 completeOnboarding、api/legalWorkspace.js:18/27）
    if (method === 'POST' && path === '/auth/complete-onboarding') return route.fulfill({ json: success({ completed: true }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/matters/3/activity') return route.fulfill({ json: success([]) })

    // 合同台账 tab
    if (method === 'GET' && path === '/legal/features') return route.fulfill({ json: success({ signing_enabled: false }) })
    if (method === 'GET' && path === '/legal/orgs/5/contracts') {
      requests.listContracts = url.searchParams.get('case_id')
      requests.reviewStatus = url.searchParams.get('review_status')
      requests.risk = url.searchParams.get('risk')
      requests.sort = url.searchParams.get('sort')
      requests.order = url.searchParams.get('order')
      requests.cursor = url.searchParams.get('cursor')
      requests.q = url.searchParams.get('q')
      const reviewStatus = url.searchParams.get('review_status')
      const q = requests.q
      if (reviewStatus || q) {
        let items = CONTRACTS
        if (reviewStatus) items = items.filter((item) => item.review?.status === reviewStatus)
        if (q) {
          items = items.filter((item) =>
            [item.title, item.counterparty, item.contract_no].some((value) => value.includes(q)))
        }
        return route.fulfill({ json: success({
          items, total: items.length, page_size: 2, has_more: false, next_cursor: null,
        }) })
      }
      if (requests.cursor === 'page-2') {
        return route.fulfill({ json: success({
          items: [CONTRACTS[2]], total: 3, page_size: 2, has_more: false, next_cursor: null,
        }) })
      }
      return route.fulfill({ json: success({
        items: CONTRACTS.slice(0, 2), total: 3, page_size: 2,
        has_more: true, next_cursor: 'page-2',
      }) })
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
  // 案件工作区 tab 现由 URL query 驱动（LegalWorkspace.vue:289）
  await page.goto('/legal-workspace?case_id=3&tab=contracts')

  // 列表（带 case 过滤）+ 签署禁用（特性开关 false）
  await expect(page.getByText('劳动合同模板联调')).toBeVisible()
  await expect(page.getByText('采购协议联调')).toBeVisible()
  // 台账跨案件视图：最近审查状态 + 严重度细分 + 审查时间
  const ledgerRow = page.locator('.el-table__row', { hasText: '劳动合同模板联调' })
  await expect(ledgerRow).toContainText('需律师审核')
  await expect(ledgerRow).toContainText('共 5 条')
  await expect(ledgerRow).toContainText('高 2（含严重 1）')
  await expect(ledgerRow).toContainText('中 2')
  await expect(ledgerRow).toContainText('低 1')
  await expect(ledgerRow).toContainText('无定位 2')
  await expect(ledgerRow).toContainText('2026-08-07 09:30')
  await expect(page.locator('.el-table__row', { hasText: '采购协议联调' })).toContainText('未审查')
  expect(requests.listContracts).toBe('3')
  expect(requests.sort).toBe('updated_at')
  expect(requests.order).toBe('desc')

  // 排序：审查时间（新→旧 / 旧→新）下推 sort/order 参数
  const sortSelect = page.locator('.contract-filters select[aria-label="合同排序"]')
  await sortSelect.selectOption('reviewed_at_desc')
  await expect.poll(() => requests.sort).toBe('reviewed_at')
  expect(requests.order).toBe('desc')
  await sortSelect.selectOption('reviewed_at_asc')
  await expect.poll(() => requests.order).toBe('asc')

  // 风险筛选：严重度细分值可下推
  const riskFilter = page.locator('.contract-filters select[aria-label="按风险筛选"]')
  await riskFilter.selectOption('critical')
  await expect.poll(() => requests.risk).toBe('critical')
  await riskFilter.selectOption('')
  await sortSelect.selectOption('updated_at_desc')
  await expect.poll(() => requests.sort).toBe('updated_at')

  if (SHOT_DIR) {
    // 窄栏台账横向滚动到右侧，截图同时保留顶部筛选与「最近审查 / 审查时间 / 严重度分布」列。
    await page.evaluate(() => {
      const wrap = document.querySelector('.el-table__body-wrapper .el-scrollbar__wrap')
      if (wrap) wrap.scrollLeft = 620
    })
    await page.waitForTimeout(200)
    await page.screenshot({ path: path.join(SHOT_DIR, 'step45-contract-ledger-severity.png'), fullPage: true })
  }

  // keyset 加载更多：带上一页 next_cursor，追加而不是重置；游标用完按钮消失
  await page.evaluate(() => {
    const wrap = document.querySelector('.el-table__body-wrapper .el-scrollbar__wrap')
    if (wrap) wrap.scrollLeft = 0
  })
  expect(requests.cursor).toBeNull()
  await expect(page.getByRole('button', { name: '加载更多', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '加载更多', exact: true }).click()
  await expect.poll(() => requests.cursor).toBe('page-2')
  await expect(page.locator('.el-table__row')).toHaveCount(3)
  await expect(page.getByText('补充协议联调')).toBeVisible()
  await expect(page.getByRole('button', { name: '加载更多', exact: true })).toHaveCount(0)

  // 搜索：q 下推（名称/相对方/编号），清空后回到第一页
  const search = page.locator('.contract-search')
  await search.fill('采购')
  await expect.poll(() => requests.q).toBe('采购')
  await expect(page.locator('.el-table__row')).toHaveCount(1)
  await expect(page.locator('.el-table__row')).toContainText('采购协议联调')
  await search.fill('')
  await expect.poll(() => requests.q).toBeNull()
  await expect(page.locator('.el-table__row')).toHaveCount(2)

  // 台账筛选：按审查状态（服务端过滤）
  const reviewFilter = page.locator('.contract-filters select[aria-label="按审查状态筛选"]')
  await reviewFilter.selectOption('needs_lawyer_review')
  await expect.poll(() => requests.reviewStatus).toBe('needs_lawyer_review')
  await expect(page.locator('.el-table__row')).toHaveCount(1)
  await expect(page.locator('.el-table__row')).toContainText('劳动合同模板联调')
  await reviewFilter.selectOption('')
  await expect(page.locator('.el-table__row')).toHaveCount(2)
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
  await page.getByRole('button', { name: '版本对比', exact: true }).first().click()
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
