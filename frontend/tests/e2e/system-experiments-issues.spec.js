import path from 'node:path'
import { test, expect } from '@playwright/test'

// System「实验观测」证据卡 → 引用质量问题下钻（step51）：
//   打开清单 → 按问题类型过滤 → 从法源行进入该法源的答复 → 展开引用明细。
// 全 mock，锁定列表字段与查询参数；真实数据口径见 docs/review/step51。
const success = (data) => ({ success: true, data })
const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

const EVIDENCE_STATS = {
  window_days: 30,
  total_consultations: 50,
  with_evidence: 3,
  with_evidence_generated: 2,
  with_evidence_recomputed: 1,
  without_evidence: 47,
  evidence_coverage: 0.06,
  levels: [
    { level: 'strong', label: '引用已逐条核对', count: 1, share: 0.3333 },
    { level: 'partial', label: '部分引用已核对', count: 1, share: 0.3333 },
    { level: 'weak', label: '引用待核', count: 1, share: 0.3333 },
    { level: 'none', label: '未引用法源', count: 0, share: 0 },
  ],
  references: {
    references: 4, article_checked: 2, article_verified: 1, article_missing: 1,
    no_corpus: 1, no_numbers: 1, unmatched: 1, materials: 0,
  },
  checked_rate: 0.5,
  trend: [],
  sources: [
    { source_id: 10, title: '劳动合同法', references: 29, consultations: 9, verified: 9, no_corpus: 0, articles: 98 },
  ],
  sources_total: 1,
  corpus_gaps: [],
  unmatched_references: { references: 1, consultations: 1 },
}

const ISSUES = {
  window_days: 30,
  total: 2,
  returned: 2,
  items: [
    {
      id: 65,
      user_name: 'pilot01',
      question: '试用期最长可以约定多久？',
      category: 'labor',
      status: 'pending_review',
      created_at: '2026-10-27T11:30:00',
      level: 'weak',
      level_label: '引用待核',
      issues: ['missing', 'weak'],
      issue_labels: ['条号未找到', '答复待核'],
      reference_count: 1,
      resolved: false,
      resolved_at: null,
      resolved_by_name: null,
      references: [{
        source_id: 10, title: '中华人民共和国劳动合同法', citation: '劳动合同法第99条',
        scope: 'missing', scope_label: '条号未找到', note: '未在已收录的 98 条条文中找到第99条',
      }],
    },
    {
      id: 64,
      user_name: 'pilot01',
      question: '公司没有签书面合同，能主张二倍工资吗？',
      category: 'labor',
      status: 'pending_review',
      created_at: '2026-10-27T10:10:00',
      level: 'partial',
      level_label: '部分引用已核对',
      issues: ['unmatched'],
      issue_labels: ['未挂法源'],
      reference_count: 2,
      resolved: false,
      resolved_at: null,
      resolved_by_name: null,
      references: [{
        source_id: null, title: '未挂法源的引用', citation: '某地方性规定',
        scope: 'no_source', scope_label: '未挂法源', note: '引用没有 source_id',
      }],
    },
  ],
}

test('System 实验观测：引用质量问题下钻', async ({ page }) => {
  const requests = {}
  let issueItems = ISSUES.items.map((row) => ({ ...row }))
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'system-evidence-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 3, username: 'admin', role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/health') return route.fulfill({ json: success({ status: 'ok', checks: {} }) })
    if (method === 'GET' && path === '/analytics/retrieval-index/stats') return route.fulfill({ json: success({ enabled: true, index_ready: true, stale: false, builds: 0, build_failures: 0, stale_discarded: 0, ttl_rebuilds: 0, fallback_scans: 0 }) })
    if (method === 'GET' && path === '/analytics/experiments/overview') {
      return route.fulfill({ json: success({ artifact_status: {}, summary: {}, experiments: [], rollouts: { items: [] }, prompt_traffic: { items: [] } }) })
    }
    if (method === 'GET' && path === '/analytics/consultations/evidence-stats') {
      requests.statsDays = url.searchParams.get('days')
      return route.fulfill({ json: success(EVIDENCE_STATS) })
    }
    if (method === 'GET' && path === '/analytics/consultations/evidence-issues') {
      requests.issueQuery = Object.fromEntries(url.searchParams.entries())
      const issue = url.searchParams.get('issue')
      const sourceId = url.searchParams.get('source_id')
      const status = url.searchParams.get('status') || 'open'
      let items = issueItems
      if (status === 'open') items = items.filter((row) => !row.resolved)
      if (status === 'resolved') items = items.filter((row) => row.resolved)
      if (issue) items = items.filter((row) => row.issues.includes(issue))
      if (sourceId) items = items.filter((row) => row.references.some((ref) => String(ref.source_id) === sourceId))
      return route.fulfill({ json: success({ window_days: 30, total: items.length, returned: items.length, items }) })
    }
    if (method === 'GET' && path === '/analytics/consultations/evidence-issues/export') {
      requests.exportDays = url.searchParams.get('days')
      return route.fulfill({
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="citation-corpus-gaps-20261027.csv"' },
        contentType: 'text/csv; charset=utf-8',
        body: '\ufeff法源ID,法源标题\n10,劳动合同法\n',
      })
    }
    if (method === 'POST' && path === '/analytics/consultations/evidence-issues/notify') {
      requests.notifyQuery = Object.fromEntries(url.searchParams.entries())
      return route.fulfill({ json: success({ users: 1, notified_items: 1, events: 2, unresolved_total: 1, orgs: 1 }) })
    }
    if (method === 'POST' && path === '/analytics/consultations/evidence-issues/65/resolve') {
      requests.resolve = 65
      issueItems = issueItems.map((row) => (row.id === 65
        ? { ...row, resolved: true, resolved_at: '2026-10-27T12:00:00', resolved_by_name: 'admin' }
        : row))
      return route.fulfill({ json: success({ consultation_id: 65, resolved: true }) })
    }
    if (method === 'POST' && path === '/analytics/consultations/evidence-issues/65/reopen') {
      requests.reopen = 65
      issueItems = issueItems.map((row) => (row.id === 65
        ? { ...row, resolved: false, resolved_at: null, resolved_by_name: null }
        : row))
      return route.fulfill({ json: success({ consultation_id: 65, resolved: false }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/system?tab=experiments')

  await expect(page.getByText('证据可核验性（在线指标）')).toBeVisible()
  await expect(page.getByText('查看引用问题')).toBeVisible()
  expect(requests.statsDays).toBe('30')

  // 导出语料缺口清单（CSV 下载）与批量通知
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出缺口清单' }).click({ force: true }),
  ])
  expect(download.suggestedFilename()).toContain('.csv')
  expect(requests.exportDays).toBe('30')
  await page.getByRole('button', { name: '通知负责人' }).click({ force: true })
  await expect.poll(() => requests.notifyQuery).toBeTruthy()
  expect(requests.notifyQuery.days).toBe('30')
  await expect(page.getByText(/已通知 1 位管理员/)).toBeVisible()

  // 打开引用质量问题清单（默认未处理）
  await page.getByRole('button', { name: '查看引用问题' }).click()
  await expect.poll(() => requests.issueQuery, { timeout: 5000 }).toBeTruthy()
  expect(requests.issueQuery).toEqual({ days: '30', limit: '50', status: 'open' })
  await expect(page.getByText('引用质量问题清单')).toBeVisible()
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(2)
  await expect(page.locator('.evidence-issues')).toContainText('试用期最长可以约定多久？')
  await expect(page.locator('.evidence-issues')).toContainText('条号未找到 / 答复待核')

  // 标记已处理 → 已处理视图 → 重新打开
  await page.locator('.evidence-issues .el-table__row').first()
    .getByRole('button', { name: '标记已处理' }).evaluate((element) => element.click())
  await expect.poll(() => requests.resolve).toBe(65)
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(1)
  await page.getByRole('radio', { name: '已处理' }).click({ force: true })
  await expect.poll(() => requests.issueQuery.status).toBe('resolved')
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(1)
  await expect(page.locator('.evidence-issues')).toContainText('已处理 · admin')
  await page.getByRole('button', { name: '重新打开' }).evaluate((element) => element.click())
  await expect.poll(() => requests.reopen).toBe(65)
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(0)
  await page.getByRole('radio', { name: '未处理' }).click({ force: true })
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(2)

  // 按问题类型过滤（顶部导航是 sticky 的，force 避免被它拦住点击）
  await page.getByRole('radio', { name: '条号未找到' }).click({ force: true })
  await expect.poll(() => requests.issueQuery.issue).toBe('missing')
  await expect(page.locator('.evidence-issues .el-table__row')).toHaveCount(1)

  // 从法源表进入：应重置问题类型，只带 source_id
  const sourceRow = page.locator('.el-table__row', { hasText: '劳动合同法' }).first()
  await sourceRow.getByRole('button', { name: '查看', exact: true }).click({ force: true })
  await expect.poll(() => requests.issueQuery.source_id).toBe('10')
  expect(requests.issueQuery.issue).toBeUndefined()
  await expect(page.getByText('引用质量：劳动合同法')).toBeVisible()

  // 展开行看引用明细（直接触发 DOM click，避开 sticky 顶栏遮挡）
  await page.locator('.evidence-issues .el-table__row').first()
    .locator('.el-table__expand-icon')
    .evaluate((element) => element.click())
  await expect(page.locator('.issue-ref-list')).toContainText('未在已收录的 98 条条文中找到第99条')

  if (SHOT_DIR) {
    // 截图只看证据卡：隐藏 sticky 顶栏，并把被「查看」按钮带偏的表格横向滚动复位
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.evaluate(() => {
      document.querySelectorAll('.el-table__body-wrapper .el-scrollbar__wrap')
        .forEach((wrap) => { wrap.scrollLeft = 0 })
    })
    await page.waitForTimeout(200)
    await page.locator('section.app-card', { hasText: '证据可核验性（在线指标）' }).first()
      .screenshot({ path: path.join(SHOT_DIR, 'step51-evidence-issues.png') })
  }

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
