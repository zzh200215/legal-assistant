import { test, expect } from '@playwright/test'

// U-3 一键流转浏览器实测：咨询→提交律师审核→合同审查→文书起草→审核队列通过。
// 三个 AI 创建接口被 mock（避免依赖 LLM），每个请求的载荷都被断言；
// 覆盖结果卡渲染、状态流转按钮、审核队列操作弹窗等 UI 接线。
const success = (data) => ({ success: true, data })

const CONSULT_ROW = {
  id: 991,
  target_type: 'consultation',
  question: 'U3探针：公司辞退我未支付经济补偿金，如何维权？',
  case_id: 3,
  category: 'labor_dispute',
  risk_level: 'medium',
  status: 'needs_lawyer_review',
  confidence: 82,
  known_facts: ['工作3年被辞退'],
  missing_facts: [],
  references: [],
  advice: '公司单方解除劳动合同应依法支付经济补偿。',
  disclaimer_level: 'standard',
  // SLA：6 小时后到期 → 审核队列应显示「24h内到期 / 剩 6 小时」
  review_due_at: '2026-10-22T10:00:00',
  review_due_in_hours: 6,
  sla_state: 'due_soon',
  review_overdue: false,
  // 咨询侧证据链摘要（服务端派生）：1 条有效引用、4 句里 3 句有溯源、忠实度通过
  evidence_summary: {
    references: 1, active_references: 1, sentences: 4, attributed: 3,
    attributed_rate: 0.75, faithfulness: 'pass', unsupported: 0, level: 'partial',
  },
}

const CONTRACT_ROW = {
  id: 992,
  target_type: 'contract_review',
  title: 'U3探针：技术服务合同',
  content: '甲方应按期支付服务费，但付款不以验收为前提。',
  case_id: 3,
  status: 'needs_lawyer_review',
  summary: '付款条款存在高风险，需人工复核。',
  confidence: 78,
  risks: [{
    id: 1, label: '付款条款', clause_type: 'payment', risk_level: 'medium', status: 'open',
    description: '付款节点缺少验收条件。', suggestion: '补充验收标准与付款条件。',
    source_location: { paragraph: null, snippet: '' },
    evidence: { anchored: false, paragraph: null, snippet_verified: null },
  }],
  // 服务端读时下发的证据链统计：1 条风险、0 条有定位 → 审核通过前必须人工核对
  evidence_summary: { total: 1, anchored: 0, unanchored: 1, unanchored_high: 0, unanchored_by_level: { medium: 1 }, snippets: 0, verified: 0, located_rate: 0, snippet_rate: null, level: 'none' },
  references: [],
}

const HIGH_GAP_ROW = {
  id: 994,
  target_type: 'contract_review',
  title: 'U3探针：高风险无定位合同',
  content: '双方约定违约金按日千分之五计算。',
  case_id: 3,
  status: 'needs_lawyer_review',
  summary: '违约金条款风险极高。',
  confidence: 70,
  risks: [{
    id: 2, label: '违约责任', clause_type: 'breach', risk_level: 'high', status: 'open',
    description: '违约金比例过高。', suggestion: '调整为 LPR 四倍以内。',
    source_location: { paragraph: null, snippet: '' },
    evidence: { anchored: false, paragraph: null, snippet_verified: null },
  }],
  // 高风险缺口：勾选也不能放行，必须补齐定位
  evidence_summary: { total: 1, anchored: 0, unanchored: 1, unanchored_high: 1, unanchored_by_level: { high: 1 }, snippets: 0, verified: 0, located_rate: 0, snippet_rate: null, level: 'none' },
  references: [],
}

const DRAFT_ROW = {
  id: 993,
  title: '劳动争议仲裁申请书（U3探针）',
  content: '申请人：张三……仲裁请求：支付经济补偿金。',
  case_id: 3,
  status: 'pending_review',
  missing_fields: [],
  confidence: 75,
  references: [],
}

test('U-3 一键流转：咨询→提交审核→合同审查→起草→审核队列通过', async ({ page }) => {
  const requests = {}
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
      expect(request.postDataJSON()).toEqual({ username: 'pilot_lawyer', password: 'test-password' })
      await route.fulfill({ json: success({ access_token: 'u3-e2e-token' }) })
      return
    }
    if (method === 'GET' && path === '/auth/me') {
      // Step 4 需要「审核决定」按钮：canReview = workspace.review capability 或 legal_role∈{admin,reviewer}
      // （LegalReviewTab.vue:100，DEPT_ADMIN_CAPS 见 capabilities.js:38-42）；
      // 门户面板同样只看 legal_role（LegalPortalTab.vue:206-207 canManagePortal），故一并给出 reviewer
      await route.fulfill({ json: success({ id: 7, username: 'pilot_lawyer', email: 'pilot@example.com', role: 'dept_admin', legal_role: 'reviewer', status: 'active' }) })
      return
    }
    if (method === 'GET' && path === '/developer/onboarding') {
      await route.fulfill({ json: success({ user_role: 'solo_lawyer', completed_steps_json: '[]' }) })
      return
    }
    // 通知铃铛首屏加载（US-002）
    if (method === 'GET' && path === '/developer/notifications/me') {
      await route.fulfill({ json: success({ items: [], unread: 0 }) })
      return
    }

    if (request.headers().authorization !== 'Bearer u3-e2e-token') {
      unexpectedApiRequests.push(`missing authorization: ${method} ${path}`)
      await route.fulfill({ status: 401, json: { detail: 'missing authorization' } })
      return
    }

    if (method === 'GET' && path === '/legal/overview') {
      await route.fulfill({ json: success({ organization_id: 5, brand: '律智检', workflows: [{ key: 'consultation', label: '法律咨询' }] }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases') {
      await route.fulfill({ json: success([{ id: 3, title: '联调测试案件', status: 'in_progress', case_type: 'labor_dispute', organization_id: 5 }]) })
      return
    }
    if (method === 'GET' && path === '/billing/subscriptions/quota') {
      await route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/portal-branding') {
      requests.portalBranding = path
      await route.fulfill({ json: success({ portal_logo_url: '', portal_welcome_message: '' }) })
      return
    }

    // U-3 step 1: 咨询创建 + 提交律师审核
    if (method === 'POST' && path === '/legal/consultations') {
      requests.consult = request.postDataJSON()
      await route.fulfill({ json: success({ ...CONSULT_ROW, status: 'pending_review' }) })
      return
    }
    if (method === 'POST' && path === '/legal/review-queue/consultation/991/actions') {
      const body = request.postDataJSON()
      if (body?.action === 'submit_review') {
        requests.consultSubmitReview = body
        await route.fulfill({ json: success({ ...CONSULT_ROW, status: 'needs_lawyer_review' }) })
      } else if (body?.action === 'approve') {
        requests.consultApprove = body
        await route.fulfill({ json: success({ ...CONSULT_ROW, status: 'lawyer_approved' }) })
      } else {
        unexpectedApiRequests.push(`${method} ${path} action=${body?.action}`)
        await route.fulfill({ status: 500, json: { detail: `Unhandled review action: ${body?.action}` } })
      }
      return
    }

    // U-3 step 2: 合同审查
    if (method === 'POST' && path === '/legal/contract-reviews') {
      requests.contract = request.postDataJSON()
      await route.fulfill({ json: success(CONTRACT_ROW) })
      return
    }

    // U-3 step 3: 文书起草
    if (method === 'POST' && path === '/legal/drafts') {
      requests.draft = request.postDataJSON()
      await route.fulfill({ json: success(DRAFT_ROW) })
      return
    }

    // U-3 step 4: 审核队列通过（与 submit_review 共用上面同一个 matcher，按 action 分发）

    if (method === 'GET' && path === '/legal/consultations') {
      await route.fulfill({ json: success([CONSULT_ROW]) })
      return
    }
    if (method === 'GET' && path === '/legal/contract-reviews') {
      // 新 UI 在挂载时自动展开最新一条审查记录（LegalContractTab.vue:377-389 openLatestContractReview），
      // 有记录时不再渲染录入表单（同文件:3 v-if="!contractResult"）；这里按「创建前后」返回，保持与新契约一致。
      await route.fulfill({ json: success(requests.contract ? [CONTRACT_ROW] : []) })
      return
    }
    if (method === 'GET' && path === '/legal/drafts') {
      await route.fulfill({ json: success([DRAFT_ROW]) })
      return
    }
    if (method === 'GET' && path === '/legal/document-templates') {
      await route.fulfill({ json: success([{ key: 'labor_arbitration_application', label: '劳动争议仲裁申请书' }]) })
      return
    }
    if (method === 'GET' && path === '/legal/sources') {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue') {
      await route.fulfill({ json: success([CONSULT_ROW, CONTRACT_ROW, HIGH_GAP_ROW]) })
      return
    }
    if (method === 'GET' && path === '/legal/review-stats') {
      await route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue/reviewers') {
      // only canReview 账号才会加载审核人列表（LegalReviewTab.vue:185，canReview 见 capabilities.js:16/:38-42）
      await route.fulfill({ json: success([{ user_id: 7, name: 'pilot_lawyer', role: 'reviewer' }]) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue/consultation/991/history') {
      await route.fulfill({ json: success({ history: [] }) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue/contract_review/992/history') {
      await route.fulfill({ json: success({ history: [] }) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue/contract_review/994/history') {
      await route.fulfill({ json: success({ history: [] }) })
      return
    }
    if (method === 'POST' && path === '/legal/review-queue/contract_review/992/actions') {
      requests.contractApprove = request.postDataJSON()
      await route.fulfill({ json: success({ ...CONTRACT_ROW, status: 'lawyer_approved' }) })
      return
    }

    // 客户门户按需加载（lazy）：点开 tab 时才请求，且必须使用正确 org 5
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/portal-links') {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/progress-updates') {
      await route.fulfill({ json: success({ items: [], total: 0 }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/members') {
      await route.fulfill({ json: success([{ user_id: 7, case_role: 'owner' }]) })
      return
    }

    // HEAD 工作台在案件/合同/文书/门户面板上的既有只读请求（与 pilot-workflow.spec.js 同一组）：
    // 概览条目与案件动态（LegalWorkspace.vue:325-337）、合同版本与风险项（LegalContractTab.vue:243-249）、
    // 文书版本/批注/协作（useLegalDrafts.js）、门户访问分析（LegalPortalTab.vue:272）、引导自动完成（LegalOnboarding.vue:67）
    if (method === 'POST' && path === '/auth/complete-onboarding') {
      await route.fulfill({ json: success({ completed: true }) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') {
      await route.fulfill({ json: success({ consultations: [CONSULT_ROW], contract_reviews: [CONTRACT_ROW], drafts: [DRAFT_ROW] }) })
      return
    }
    if (method === 'GET' && path === '/legal/matters/3/activity') {
      await route.fulfill({ json: success({ items: [] }) })
      return
    }
    if (method === 'GET' && path === '/legal/contract-reviews/992/versions') {
      await route.fulfill({ json: success([{ id: 992, version: 1, content: CONTRACT_ROW.content, created_at: '2026-01-01T00:00:00Z' }]) })
      return
    }
    if (method === 'GET' && path === '/legal/contract-reviews/992/risk-items') {
      await route.fulfill({ json: success(CONTRACT_ROW.risks) })
      return
    }
    if (method === 'GET' && path === '/legal/drafts/993/versions') {
      await route.fulfill({ json: success([{ id: 993, version: 1, content: DRAFT_ROW.content, created_at: '2026-01-01T00:00:00Z' }]) })
      return
    }
    if (method === 'GET' && path === '/legal/drafts/993/comments') {
      await route.fulfill({ json: success([]) })
      return
    }
    if (method === 'GET' && path === '/legal/drafts/993/collaboration') {
      await route.fulfill({ json: success({}) })
      return
    }
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/portal-analytics') {
      await route.fulfill({ json: success({ summary: { visits: 0, unique_visitors: 0, active_links: 0, denied: 0 }, daily: [] }) })
      return
    }

    unexpectedApiRequests.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled E2E API request: ${method} ${path}` } })
  })

  // 登录
  await page.goto('/login')
  const loginInputs = page.locator('.login-form input')
  await loginInputs.nth(0).fill('pilot_lawyer')
  await loginInputs.nth(1).fill('test-password')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/legal-onboarding/)
  await page.getByRole('button', { name: /进入工作台/ }).click()
  await expect(page).toHaveURL(/\/legal-workspace/)

  // 案件工作区由 URL query 驱动（LegalWorkspace.vue:289/:308-323）：直达案件 3 的法律咨询
  await page.goto('/legal-workspace?case_id=3&tab=consultation')

  // Step 1: 咨询 → 提交律师审核
  await page.getByPlaceholder(/例如：我在公司工作了3年/).fill(CONSULT_ROW.question)
  await page.getByRole('button', { name: '提交咨询', exact: true }).click()
  await expect(page.getByText('公司单方解除劳动合同应依法支付经济补偿。')).toBeVisible()
  // 咨询侧证据链：结果卡显示句级溯源与忠实度，历史列表有「依据」列
  await expect(page.locator('.chain-line')).toContainText('句级溯源 3/4 句')
  await expect(page.locator('.history-card')).toContainText('依据')
  await expect(page.locator('.history-card')).toContainText('1 条引用 · 3/4 句')
  expect(requests.consult).toEqual({ question: CONSULT_ROW.question, case_id: 3 })

  await page.getByRole('button', { name: '提交律师审核', exact: true }).click()
  await page.getByRole('button', { name: '确认提交', exact: true }).click()
  await expect(page.getByText('已提交律师审核队列')).toBeVisible()
  expect(requests.consultSubmitReview).toEqual({ action: 'submit_review', note: '用户提交审核' })

  // Step 2: 合同审查（自动进入审核队列）
  await page.locator('.legal-matter-tab', { hasText: '合同审查' }).click()
  await page.getByRole('textbox', { name: '合同名称' }).fill(CONTRACT_ROW.title)
  await page.getByPlaceholder(/粘贴合同全文或主要条款/).fill(CONTRACT_ROW.content)
  await page.getByRole('button', { name: '开始审查', exact: true }).click()
  await expect(page.getByText('付款条款存在高风险，需人工复核。')).toBeVisible()
  await expect(page.getByText('补充验收标准与付款条件。')).toBeVisible()
  expect(requests.contract).toEqual({ title: CONTRACT_ROW.title, content: CONTRACT_ROW.content, case_id: 3 })

  // Step 3: 文书起草
  await page.locator('.legal-matter-tab', { hasText: '文书' }).click()
  await page.locator('.el-select:has-text("选择文书类型")').click()
  await page.getByRole('option', { name: '劳动争议仲裁申请书' }).click()
  // 字段随文书类型动态渲染（LegalDraftsTab.vue:11-13），必填字段 placeholder 为「请输入X」、可选字段为「可选」，
  // 故按 label 关联的 id 定位（字段清单见同文件 labor_arbitration_application 的默认定义）。
  const fieldValues = {
    申请人: '张三',
    被申请人: '某科技公司',
    劳动关系起止时间: '2020-01-01 至 2026-07-31',
    仲裁请求: '支付经济补偿金 30000 元',
    事实与理由: '无故辞退',
    证据清单: '劳动合同、工资流水',
  }
  for (const [field, value] of Object.entries(fieldValues)) {
    await page.locator(`#draft-field-${field}`).fill(value)
  }
  await page.getByRole('button', { name: '生成草稿', exact: true }).click()
  await expect(page.getByText('劳动争议仲裁申请书（U3探针）').first()).toBeVisible()
  // 旧 UI 的「字段完整」徽标在 HEAD 已移除（源码中无此文案）；新 UI 只在缺字段时渲染「待补充事实」
  // （LegalDraftsTab.vue:36），因此以「不出现待补充事实」等价断言字段齐全，字段取值仍由下面的载荷深比较覆盖。
  await expect(page.locator('.missing-facts')).toHaveCount(0)
  expect(requests.draft).toEqual({
    document_type: 'labor_arbitration_application',
    fields: {
      申请人: '张三', 被申请人: '某科技公司', 劳动关系起止时间: '2020-01-01 至 2026-07-31',
      仲裁请求: '支付经济补偿金 30000 元', 事实与理由: '无故辞退', 证据清单: '劳动合同、工资流水',
    },
    case_id: 3,
  })

  // Step 4: 审核队列通过（新 UI 的队列项是 .queue-item 按钮并自动选中首项，决定按钮在详情 footer：
  // LegalReviewTab.vue:28 列表、:174-183 自动选中、:71 通过按钮）
  await page.locator('.legal-matter-tab', { hasText: '审核' }).click()
  const consultationQueueItem = page.locator('.queue-item', { hasText: CONSULT_ROW.question })
  await expect(consultationQueueItem).toBeVisible()
  // SLA 提醒：24h 内到期标签 + 相对剩余时间
  await expect(consultationQueueItem.getByText('24h内到期')).toBeVisible()
  await expect(consultationQueueItem.getByText('剩 6 小时')).toBeVisible()
  await expect(page.locator('.review-actions')).toBeVisible()
  await page.locator('.review-actions').getByRole('button', { name: '通过', exact: true }).click()
  const promptDialog = page.getByRole('dialog')
  await promptDialog.locator('input, textarea').last().fill('事实清楚，予以通过')
  await promptDialog.getByRole('button', { name: '确认' }).click()
  await expect(page.getByText('操作成功')).toBeVisible()
  expect(requests.consultApprove).toEqual({ action: 'approve', note: '事实清楚，予以通过' })

  // Step 5: 合同审查的证据链门禁——无条款定位必须先勾选人工核对才能通过
  await page.locator('.queue-item', { hasText: CONTRACT_ROW.title }).click()
  await expect(page.getByText('证据链缺口')).toBeVisible()
  const contractApprove = page.locator('.review-actions').getByRole('button', { name: '通过', exact: true })
  await expect(contractApprove).toBeDisabled()
  await page.getByRole('checkbox', { name: /已人工核对无定位风险/ }).check()
  await expect(contractApprove).toBeEnabled()
  await contractApprove.click()
  const gateDialog = page.getByRole('dialog')
  await gateDialog.locator('input, textarea').last().fill('已核对无定位风险，确认通过')
  await gateDialog.getByRole('button', { name: '确认' }).click()
  await expect.poll(() => requests.contractApprove).toMatchObject({
    action: 'approve',
    acknowledge_evidence_gaps: true,
  })

  // Step 6: 高风险缺口不可勾选放行——必须补齐定位
  await page.locator('.queue-item', { hasText: HIGH_GAP_ROW.title }).click()
  await expect(page.getByText('必须回到合同页补齐定位')).toBeVisible()
  await expect(page.locator('.evidence-gap-block.is-blocking')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /已人工核对无定位风险/ })).toHaveCount(0)
  await expect(page.locator('.review-actions').getByRole('button', { name: '通过', exact: true })).toBeDisabled()

  // 门户 tab 按需加载：点开后 branding 请求使用正确 org 5（而非默认 org 1）
  // 客户门户在「更多案件工具」下拉里（LegalWorkspace.vue:70-75，非 tab 按钮）
  await page.locator('.matter-tools-select').selectOption('portal')
  await expect.poll(() => requests.portalBranding).toBe('/legal/orgs/5/portal-branding')
  // CaseHeader（LegalWorkspace.vue:58）也渲染「负责人」，限定在门户面板内断言
  await expect(page.locator('.matter-panel').getByText('负责人')).toBeVisible()

  expect(unexpectedApiRequests).toEqual([])
  expect(failedResponses).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
