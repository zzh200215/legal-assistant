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
}

const CONTRACT_ROW = {
  id: 992,
  title: 'U3探针：技术服务合同',
  content: '甲方应按期支付服务费，但付款不以验收为前提。',
  case_id: 3,
  status: 'needs_lawyer_review',
  summary: '付款条款存在高风险，需人工复核。',
  confidence: 78,
  risks: [{
    id: 1, label: '付款条款', clause_type: 'payment', risk_level: 'high', status: 'open',
    description: '付款节点缺少验收条件。', suggestion: '补充验收标准与付款条件。',
    source_location: { paragraph: 1 },
  }],
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
      await route.fulfill({ json: success({ id: 7, username: 'pilot_lawyer', email: 'pilot@example.com', role: 'user', status: 'active' }) })
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
      await route.fulfill({ json: success([CONTRACT_ROW]) })
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
      await route.fulfill({ json: success([CONSULT_ROW]) })
      return
    }
    if (method === 'GET' && path === '/legal/review-stats') {
      await route.fulfill({ json: success({ total_actions: 0, action_distribution: {}, target_type_distribution: {}, return_reasons: [], recent_actions: [] }) })
      return
    }
    if (method === 'GET' && path === '/legal/review-queue/consultation/991/history') {
      await route.fulfill({ json: success({ history: [] }) })
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

  // 选择案件（咨询/审查/文书归档到案件 3）
  await page.locator('.case-bar .el-select').click()
  await page.getByRole('option', { name: /联调测试案件/ }).click()

  // Step 1: 咨询 → 提交律师审核
  await page.getByRole('tab', { name: '法律咨询', exact: true }).click()
  await page.getByPlaceholder(/例如：我在公司工作了3年/).fill(CONSULT_ROW.question)
  await page.getByRole('button', { name: '提交咨询', exact: true }).click()
  await expect(page.getByText('公司单方解除劳动合同应依法支付经济补偿。')).toBeVisible()
  expect(requests.consult).toEqual({ question: CONSULT_ROW.question, case_id: 3 })

  await page.getByRole('button', { name: '提交律师审核', exact: true }).click()
  await page.getByRole('button', { name: '确认提交', exact: true }).click()
  await expect(page.getByText('已提交律师审核队列')).toBeVisible()
  expect(requests.consultSubmitReview).toEqual({ action: 'submit_review', note: '用户提交审核' })

  // Step 2: 合同审查（自动进入审核队列）
  await page.getByRole('tab', { name: '合同审查', exact: true }).click()
  await page.getByRole('textbox', { name: '合同标题' }).fill(CONTRACT_ROW.title)
  await page.getByPlaceholder(/粘贴合同全文或主要条款/).fill(CONTRACT_ROW.content)
  await page.getByRole('button', { name: '开始审查', exact: true }).click()
  await expect(page.getByText('付款条款存在高风险，需人工复核。')).toBeVisible()
  await expect(page.getByText('补充验收标准与付款条件。')).toBeVisible()
  expect(requests.contract).toEqual({ title: CONTRACT_ROW.title, content: CONTRACT_ROW.content, case_id: 3 })

  // Step 3: 文书起草
  await page.getByRole('tab', { name: '文书草稿', exact: true }).click()
  await page.locator('.el-select:has-text("选择文书类型")').click()
  await page.getByRole('option', { name: '劳动争议仲裁申请书' }).click()
  const fieldValues = {
    '【必填】请输入申请人': '张三',
    '【必填】请输入被申请人': '某科技公司',
    '请输入劳动关系起止时间': '2020-01-01 至 2026-07-31',
    '【必填】请输入仲裁请求': '支付经济补偿金 30000 元',
    '请输入事实与理由': '无故辞退',
    '【必填】请输入证据清单': '劳动合同、工资流水',
  }
  for (const [placeholder, value] of Object.entries(fieldValues)) {
    await page.getByPlaceholder(placeholder).fill(value)
  }
  await page.getByRole('button', { name: '生成草稿', exact: true }).click()
  await expect(page.getByText('劳动争议仲裁申请书（U3探针）').first()).toBeVisible()
  await expect(page.getByText('字段完整')).toBeVisible()
  expect(requests.draft).toEqual({
    document_type: 'labor_arbitration_application',
    fields: {
      申请人: '张三', 被申请人: '某科技公司', 劳动关系起止时间: '2020-01-01 至 2026-07-31',
      仲裁请求: '支付经济补偿金 30000 元', 事实与理由: '无故辞退', 证据清单: '劳动合同、工资流水',
    },
    case_id: 3,
  })

  // Step 4: 审核队列通过
  await page.getByRole('tab', { name: '律师审核', exact: true }).click()
  const consultRow = page.locator('tr', { hasText: CONSULT_ROW.question }).filter({ visible: true })
  await consultRow.click()
  await consultRow.locator('button', { hasText: '通过' }).click()
  const promptDialog = page.getByRole('dialog')
  await promptDialog.locator('input, textarea').last().fill('事实清楚，予以通过')
  await promptDialog.getByRole('button', { name: '确认' }).click()
  await expect(page.getByText('操作成功')).toBeVisible()
  expect(requests.consultApprove).toEqual({ action: 'approve', note: '事实清楚，予以通过' })

  // 门户 tab 按需加载：点开后 branding 请求使用正确 org 5（而非默认 org 1）
  await page.getByRole('tab', { name: '客户门户', exact: true }).click()
  await expect.poll(() => requests.portalBranding).toBe('/legal/orgs/5/portal-branding')
  await expect(page.getByText('负责人')).toBeVisible()

  expect(unexpectedApiRequests).toEqual([])
  expect(failedResponses).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
