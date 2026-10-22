import { test, expect } from '@playwright/test'

// 合同审查文件上传联调回归：el-upload before-upload → multipart POST
// /legal/contract-reviews/upload（file/title/case_id）→ 返回审查结果渲染。
// 全 mock，锁定上传路径契约（区别于 U-3 的纯文本提交）。
const success = (data) => ({ success: true, data })

// 审查结果夹具：服务端已在读接口补好 evidence / evidence_summary（前端直接用，不重算）
const REVIEW = {
  id: 1,
  title: '联调合同.txt',
  status: 'draft',
  summary: '上传合同审查完成，共识别 2 处风险点',
  content: [
    '合同总金额为人民币一百万元。',
    '双方未约定交付标准。',
  ].join('\n'),
  risks: [
    { id: 11, clause_type: 'payment', label: '付款条款', risk_level: 'high', description: '付款节点未明确', suggestion: '补充付款节点', source_location: { paragraph: 1, snippet: '合同总金额为人民币一百万元' }, evidence: { anchored: true, paragraph: 1, snippet_verified: true } },
    { id: 12, clause_type: 'delivery', label: '交付与验收', risk_level: 'medium', description: '未约定交付标准', suggestion: '补充交付与验收条款', source_location: { paragraph: null, snippet: '' }, evidence: { anchored: false, paragraph: null, snippet_verified: null } },
  ],
  evidence_summary: { total: 2, active: 2, anchored: 1, unanchored: 1, unanchored_high: 0, unanchored_by_level: { medium: 1 }, snippets: 1, verified: 1, located_rate: 0.5, snippet_rate: 1, level: 'partial' },
  version: 1,
}

const RISK_STATS = {
  reviews: 2,
  risks: 3,
  by_severity: { critical: 0, high: 2, medium: 1, low: 0 },
  by_status: { needs_review: 2, open: 1, accepted: 0, mitigated: 0, dismissed: 0 },
  by_clause_type: [{ clause_type: 'breach', count: 2 }, { clause_type: 'payment', count: 1 }],
  anchored: 2,
  unanchored: 1,
  located_rate: 0.6667,
  high_total: 2,
  pending_high: 2,
  reviews_with_gaps: 1,
  by_evidence_level: { strong: 1, partial: 1, weak: 0, none: 0 },
  window: { days: null },
  trend: [
    { week_start: '2026-10-19', reviews: 2, risks: 3, high: 2 },
  ],
  scope: 'case',
  case_id: 3,
  generated_at: '2026-10-22T03:00:00+00:00',
}

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
    if (method === 'POST' && path === '/auth/complete-onboarding') return route.fulfill({ json: success({ completed: true }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, brand: '律智检' }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', organization_id: 5 }]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && ['/legal/consultations', '/legal/contract-reviews', '/legal/drafts', '/legal/document-templates', '/legal/sources', '/legal/review-queue'].includes(path)) return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-stats') return route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
    // 通知铃铛首屏加载（US-002）
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })

    // 案件工作区新增的读取（api/legalWorkspace.js:18/27/107/113）
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/matters/3/activity') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/contract-reviews/1/versions') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/contract-reviews/1/risk-items') {
      return route.fulfill({ json: success([
        { id: 1, index: 0, category: 'payment', severity: 'high', title: '付款条款', summary: '付款节点未明确', status: 'needs_review', can_resolve: true, history: [] },
      ]) })
    }
    if (method === 'POST' && path === '/legal/contract-reviews/1/risk-items/1/action') {
      const body = request.postDataJSON()
      requests.riskAction = body
      const base = {
        id: 1, index: 0, category: 'payment', severity: 'high', title: '付款条款', summary: '付款节点未明确',
        can_resolve: true,
      }
      const acceptEntry = { id: 7, action: 'risk_accept', note: '合同审查工作区处理', risk_index: 0, risk_title: '付款条款', actor_name: '测试律师', created_at: '2026-10-22T04:00:00' }
      if (body.action === 'reopen') {
        return route.fulfill({ json: success({
          ...base, status: 'needs_review',
          history: [{ id: 8, action: 'risk_reopen', note: '合同审查工作区处理', risk_index: 0, risk_title: '付款条款', actor_name: '测试律师', created_at: '2026-10-22T04:10:00' }, acceptEntry],
        }) })
      }
      return route.fulfill({ json: success({
        ...base, status: 'accepted', history: [acceptEntry],
      }) })
    }

    if (method === 'POST' && path === '/legal/contract-reviews/upload') {
      requests.upload = request.postDataBuffer().toString('utf8')
      return route.fulfill({ json: success(REVIEW) })
    }

    if (method === 'GET' && path === '/legal/contract-reviews/risk-stats') {
      requests.riskStats = url.searchParams.get('case_id')
      requests.riskStatsDays = url.searchParams.get('days')
      return route.fulfill({ json: success(RISK_STATS) })
    }

    if (method === 'POST' && path === '/legal/contract-reviews/1/risks/1/anchor') {
      requests.anchor = request.postDataJSON()
      const updated = JSON.parse(JSON.stringify(REVIEW))
      updated.risks[1].source_location = { paragraph: 2, snippet: REVIEW.content.split('\n')[1] }
      updated.risks[1].evidence = { anchored: true, paragraph: 2, snippet_verified: true }
      updated.evidence_summary = {
        ...REVIEW.evidence_summary,
        anchored: 2,
        unanchored: 0,
        unanchored_by_level: {},
        located_rate: 1,
        level: 'strong',
      }
      return route.fulfill({ json: success(updated) })
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
  // 案件工作区 tab 现由 URL query 驱动（LegalWorkspace.vue:289）
  await page.goto('/legal-workspace?case_id=3&tab=contract')

  // 上传文件 → multipart POST
  await page.locator('.contract-workspace input[type="file"]').setInputFiles({
    name: '联调合同.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('甲乙双方就技术服务事宜达成如下协议，本合同自双方签署之日起生效。', 'utf8'),
  })

  // 结果渲染 + 消息 + multipart 载荷含 file/case_id
  await expect(page.getByText('上传合同审查完成，共识别 2 处风险点')).toBeVisible()

  // 证据链：1 条有条款定位 / 1 条缺口；缺口的风险在列表里显式标注，不能静默
  await expect(page.locator('.evidence-chain')).toHaveText('1/2 有定位')
  await expect(page.locator('.risk-location--missing')).toHaveCount(1)
  await expect(page.locator('.evidence-gap-toggle')).toHaveText('无定位 1')

  // 缺定位的风险：详情里给出人工定位提示
  await page.locator('.risk-issue').filter({ hasText: '交付与验收' }).click()
  await expect(page.locator('.evidence-note')).toContainText('建议人工定位')

  // 缺口筛选：只看无定位，条目里不再出现有定位的那条风险
  await page.locator('.evidence-gap-toggle').click()
  await expect(page.getByText('付款节点未明确')).toHaveCount(0)
  await expect(page.locator('.risk-issue')).toHaveCount(1)
  await page.locator('.evidence-gap-toggle').click()
  await expect(page.getByText('付款节点未明确')).toBeVisible()

  // 就地补定位：点「从原文补定位」→ 点第 2 段 → 回填 source_location 并刷新证据链
  await page.locator('.risk-issue').filter({ hasText: '交付与验收' }).click()
  await page.getByRole('button', { name: '从原文补定位' }).click()
  await expect(page.getByText('请在左侧合同原文中点击要锚定的段落')).toBeVisible()
  await page.locator('#para-2').click()
  await expect.poll(() => requests.anchor).toEqual({ paragraph: 2, snippet: '双方未约定交付标准。' })
  await expect(page.locator('.evidence-chain')).toHaveText('2/2 有定位')
  await expect(page.locator('.risk-location--missing')).toHaveCount(0)
  await expect(page.locator('.evidence-gap-toggle')).toBeDisabled()

  // 逐条处置：接受风险 → 写动作接口 → 处置记录可见
  await page.locator('.risk-issue').filter({ hasText: '付款条款' }).click()
  await page.getByRole('button', { name: '接受风险' }).click()
  await expect.poll(() => requests.riskAction).toEqual({ action: 'accept', note: '合同审查工作区处理' })
  await expect(page.locator('.risk-history')).toContainText('接受风险')
  await expect(page.locator('.risk-history')).toContainText('测试律师')
  // 撤销处置：回到待审核，动作也留痕
  await page.getByRole('button', { name: '撤销处置' }).click()
  await expect.poll(() => requests.riskAction).toEqual({ action: 'reopen', note: '合同审查工作区处理' })
  await expect(page.locator('.risk-disposition')).toContainText('待审核')
  await expect(page.locator('.risk-history')).toContainText('撤销处置')

  // 跨审查风险统计：展开时才请求，数字来自服务端派生视图
  await page.locator('.risk-stats-details summary').click()
  await expect(page.locator('.risk-stats-details .stat-value').first()).toHaveText('3')
  await expect(page.locator('.risk-stats-details')).toContainText('2 条审查 · 当前案件')
  await expect(page.locator('.risk-stats-details').getByText('高/严重')).toBeVisible()
  await expect(page.locator('.severity-row').nth(1).locator('.severity-count')).toHaveText('2')
  await expect.poll(() => requests.riskStats).toBe('3')
  // 时间窗：切近7天后重新请求，摘要与趋势跟着更新
  await expect(page.locator('.trend-col')).toHaveCount(1)
  await page.getByRole('button', { name: '近7天' }).click()
  await expect.poll(() => requests.riskStatsDays).toBe('7')
  await expect(page.locator('.risk-stats-details summary')).toContainText('近7天')
  await expect(page.locator('.stats-range-btn.active')).toHaveText('近7天')
  await expect(page.getByText('合同文件审查完成')).toBeVisible()
  expect(requests.upload).toBeDefined()
  expect(requests.upload).toContain('联调合同.txt')
  expect(requests.upload).toContain('name="case_id"')

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
