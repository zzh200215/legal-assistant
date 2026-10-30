import path from 'node:path'
import { test, expect } from '@playwright/test'

// 咨询结果 → 文书 / 合同审查的案情带入（step55）：
// 目标 tab 是异步组件，prefill 必须在组件挂载、ref 出现之后应用。
// 全 mock，锁住「提示已带入」与实际字段值一致。
const success = (data) => ({ success: true, data })
const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

const CASE = { id: 1, title: '试点劳动争议案件', status: 'in_progress', case_type: 'labor_dispute', organization_id: 1 }
const CONSULT_RESULT = {
  id: 11,
  question: '公司未支付经济补偿金怎么办？',
  category: 'labor_dispute',
  advice: '可以主张经济补偿，建议先固定解除通知与工资流水。',
  known_facts: ['2021年3月入职', '2024年5月被辞退'],
  missing_facts: [],
  risk_level: 'medium',
  status: 'pending_review',
  confidence: 82,
  content_version: 'cv-old',
  references: [],
}

test('工作台：咨询结果带入文书与合同审查', async ({ page }) => {
  const requests = { consults: 0, drafts: 0, contracts: 0, followUps: 0 }
  let listRows = []
  const unexpected = []
  const failed = []
  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      failed.push(`${response.request().method()} ${new URL(response.url()).pathname} -> ${response.status()}`)
    }
  })
  await page.addInitScript(() => localStorage.setItem('token', 'workspace-prefill-token'))
  await page.addInitScript(() => {
    window.__copiedConsult = ''
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text) => { window.__copiedConsult = text } },
    })
  })

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 7, username: 'lawyer', role: 'user', legal_role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 1 }) })
    if (method === 'GET' && path === '/legal/history-counts') {
      return route.fulfill({
        json: success({
          consultations: listRows.length,
          contract_reviews: 0,
          drafts: 0,
        }),
      })
    }
    if (method === 'GET' && path === '/legal/orgs/1/cases') return route.fulfill({ json: success([CASE]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && path === '/legal/orgs/1/portal-branding') return route.fulfill({ json: success({ portal_logo_url: '', portal_welcome_message: '' }) })
    if (method === 'GET' && path === '/legal/orgs/1/cases/1/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/matters/1/activity') return route.fulfill({ json: success({ items: [] }) })
    if (method === 'GET' && path === '/legal/consultations') return route.fulfill({ json: success(listRows) })
    if (method === 'GET' && path === '/legal/consultations/11/export/markdown') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="legal_consultation_11.md"' },
        contentType: 'text/markdown; charset=utf-8',
        body: '# 法律咨询结果\n\n**当前状态：** 律师通过\n',
      })
    }
    if (method === 'POST' && path === '/legal/consultations/11/follow-up-task') {
      requests.followUps += 1
      return route.fulfill({ json: success({ id: 401, title: '跟进咨询：公司未支付经济补偿金', created: true }) })
    }
    if (method === 'GET' && path === '/legal/review-queue/consultation/11/history') {
      return route.fulfill({
        json: success({
          target_type: 'consultation', target_id: 11,
          history: [{
            id: 501, action: 'approve', note: '证据充分，同意通过',
            reviewer_name: '张律师', from_status: 'needs_lawyer_review',
            to_status: 'lawyer_approved', created_at: '2026-10-28T12:10:00',
          }],
        }),
      })
    }
    if (method === 'GET' && path === '/legal/consultations/11/exports') {
      return route.fulfill({
        json: success({
          items: [{
            id: 301, filename: 'legal_consultation_11.md', format: 'markdown',
            status_at_export: 'lawyer_approved', content_version: 'cv-new',
            created_at: '2026-10-28T12:00:00', user_name: 'lawyer',
          }],
          next_cursor: null,
          has_more: false,
          retention_days: 90,
          max_records: 20,
        }),
      })
    }
    if (method === 'GET' && path === '/legal/consultations/exports/301/download') {
      return route.fulfill({
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="legal_consultation_11.md"' },
        contentType: 'text/markdown; charset=utf-8',
        body: '# 法律咨询结果\n\n导出快照\n',
      })
    }
    if (method === 'GET' && path === '/legal/consultations/search') {
      const keyword = new URL(route.request().url()).searchParams.get('q') || ''
      const items = keyword === '经济补偿' ? [listRows[0]] : []
      return route.fulfill({
        json: success({
          items, total: items.length, page: 1, limit: 20, has_more: false,
          next_cursor: null,
        }),
      })
    }
    if (method === 'GET' && path === '/legal/consultations/11') {
      return route.fulfill({
        json: success({
          ...CONSULT_RESULT,
          status: 'lawyer_approved',
          updated_at: '2026-10-28T12:00:00',
          content_version: 'cv-new',
        }),
      })
    }
    if (method === 'GET' && path === '/legal/contract-reviews') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/drafts') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/review-queue') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/document-templates') {
      return route.fulfill({
        json: success([
          { key: 'labor_arbitration_application', label: '劳动仲裁申请书' },
          { key: 'private_lending_complaint', label: '民间借贷起诉状' },
          { key: 'consumer_complaint', label: '消费投诉书' },
        ]),
      })
    }
    if (method === 'POST' && path === '/legal/consultations') {
      requests.consults += 1
      const id = 10 + requests.consults
      // 模拟“提交后内容已更新”：列表 content_version 比结果卡新，触发一次一致性重取
      listRows = [{
        id, case_id: 1, status: 'pending_review', updated_at: '2026-10-28T12:00:00',
        content_version: 'cv-new', question: CONSULT_RESULT.question,
        advice: CONSULT_RESULT.advice, category: CONSULT_RESULT.category,
        risk_level: CONSULT_RESULT.risk_level,
        evidence_summary: { level: 'none', references: 0 },
        references: [],
      }]
      return route.fulfill({ json: success({ ...CONSULT_RESULT, id }) })
    }
    if (method === 'POST' && path === '/legal/drafts') {
      requests.drafts += 1
      return route.fulfill({ json: success({ id: 31, title: '劳动仲裁申请书', content: '草稿正文' }) })
    }
    if (method === 'POST' && path === '/legal/contract-reviews') {
      requests.contracts += 1
      return route.fulfill({ json: success({ id: 41, title: '劳动争议关联审查', status: 'needs_lawyer_review', risks: [] }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/legal-workspace?case_id=1&tab=consultation')
  await page.getByPlaceholder(/我在公司工作了3年/).fill('公司未支付经济补偿金怎么办？')
  await page.getByRole('button', { name: '提交咨询' }).click()
  await expect(page.getByText('可以主张经济补偿，建议先固定解除通知与工资流水。')).toBeVisible()
  expect(requests.consults).toBe(1)
  await expect(page).toHaveURL(/consultation_id=11/)
  // 历史列表 content_version 比结果卡新，结果卡应重取详情，隐藏旧的「提交律师审核」
  await expect(page.getByText('结果已同步')).toBeVisible()
  await expect(page.getByRole('button', { name: '提交律师审核' })).toHaveCount(0)
  await expect(page.getByText('律师通过')).toBeVisible()
  // 历史咨询搜索：按问题/答复/法源关键字过滤
  const historySearch = page.getByPlaceholder('搜索问题/答复/法源')
  await historySearch.fill('经济补偿')
  await expect(page.locator('.history-card .el-table__row')).toHaveCount(1)
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.locator('.history-card').screenshot({ path: path.join(SHOT_DIR, 'step68-consult-server-search.png') })
  }
  await historySearch.fill('不存在的关键字')
  await expect(page.getByText('没有符合筛选条件的咨询')).toBeVisible()
  await historySearch.fill('')
  await expect(page.locator('.history-card .el-table__row')).toHaveCount(1)
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.locator('.history-card').screenshot({ path: path.join(SHOT_DIR, 'step67-consult-history-search.png') })
  }
  await page.getByRole('button', { name: '复制结果' }).click()
  await expect.poll(() => page.evaluate(() => window.__copiedConsult)).toContain('当前状态：律师通过')
  await expect.poll(() => page.evaluate(() => window.__copiedConsult)).toContain('2021年3月入职；2024年5月被辞退')
  await page.getByRole('button', { name: '生成跟进任务' }).click()
  await expect.poll(() => requests.followUps).toBe(1)
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.waitForTimeout(150)
    await page.locator('.result-card').screenshot({ path: path.join(SHOT_DIR, 'step78-consult-followup-task.png') })
  }
  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '导出 Markdown' }).click(),
  ])
  expect(download.suggestedFilename()).toContain('.md')
  // 导出记录：展开后能看到快照并重新下载
  await page.locator('.export-history summary').click({ force: true })
  await expect(page.locator('.export-history')).toContainText('legal_consultation_11.md')
  await expect(page.locator('.export-history')).toContainText('lawyer')
  const [redownload] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: '下载', exact: true }).click(),
  ])
  expect(redownload.suggestedFilename()).toContain('.md')
  // 审核记录：已有的审核动作按时间倒序展示
  await page.locator('.review-history summary').click({ force: true })
  await expect(page.locator('.review-history')).toContainText('审核通过')
  await expect(page.locator('.review-history')).toContainText('张律师')
  await expect(page.locator('.review-history')).toContainText('证据充分，同意通过')
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.waitForTimeout(200)
    await page.locator('.result-card').screenshot({ path: path.join(SHOT_DIR, 'step66-consult-review-history.png') })
  }

  // 文书：prefill 应在异步组件挂载后生效
  await page.getByRole('button', { name: '生成文书' }).click()
  await expect(page).toHaveURL(/tab=draft/)
  const factsField = page.locator('[id="draft-field-事实与理由"]')
  await expect(factsField).toHaveValue('2021年3月入职；2024年5月被辞退')
  await expect(page.locator('.draft-type-select')).toContainText('劳动仲裁申请书')

  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.locator('.draft-workspace').screenshot({ path: path.join(SHOT_DIR, 'step55-workspace-prefill.png') })
  }

  // 合同审查：切回咨询后结果卡应仍在（页面级 ref，不要求重新提交），再带入
  await page.locator('.legal-matter-tab', { hasText: '法律咨询' }).click()
  await expect(page.getByText('可以主张经济补偿，建议先固定解除通知与工资流水。')).toBeVisible()
  expect(requests.consults).toBe(1)
  await expect(page).toHaveURL(/consultation_id=11/)
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.locator('.result-card').screenshot({ path: path.join(SHOT_DIR, 'step56-consult-result-retained.png') })
  }
  // 刷新后页面级 ref 丢失，但 URL 带 consultation_id，应走历史详情恢复同一结果卡
  await page.reload()
  await expect(page.getByText('可以主张经济补偿，建议先固定解除通知与工资流水。')).toBeVisible()
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.locator('.result-card').screenshot({ path: path.join(SHOT_DIR, 'step57-consult-url-restore.png') })
  }
  await page.getByRole('button', { name: '进入合同审查' }).click()
  await expect(page).toHaveURL(/tab=contract/)
  await expect(page.locator('#contract-title')).toHaveValue('劳动争议关联审查')
  await expect(page.getByPlaceholder('粘贴合同全文或主要条款...')).toHaveValue('2021年3月入职\n2024年5月被辞退')

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
