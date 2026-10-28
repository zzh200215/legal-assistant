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
  const requests = { consults: 0, drafts: 0, contracts: 0 }
  let listRows = []
  const unexpected = []
  const failed = []
  page.on('response', (response) => {
    if (response.url().includes('/api/') && response.status() >= 400) {
      failed.push(`${response.request().method()} ${new URL(response.url()).pathname} -> ${response.status()}`)
    }
  })
  await page.addInitScript(() => localStorage.setItem('token', 'workspace-prefill-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 7, username: 'lawyer', role: 'user', legal_role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 1 }) })
    if (method === 'GET' && path === '/legal/orgs/1/cases') return route.fulfill({ json: success([CASE]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success({ consultation: { quota: 8, remaining: 8 }, review: { quota: 8, remaining: 8 }, draft: { quota: 8, remaining: 8 } }) })
    if (method === 'GET' && path === '/legal/orgs/1/portal-branding') return route.fulfill({ json: success({ portal_logo_url: '', portal_welcome_message: '' }) })
    if (method === 'GET' && path === '/legal/orgs/1/cases/1/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/matters/1/activity') return route.fulfill({ json: success({ items: [] }) })
    if (method === 'GET' && path === '/legal/consultations') return route.fulfill({ json: success(listRows) })
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
  if (SHOT_DIR) {
    await page.addStyleTag({ content: '.workspace-topbar { display: none !important; } .el-message { display: none !important; }' })
    await page.waitForTimeout(200)
    await page.locator('.result-card').screenshot({ path: path.join(SHOT_DIR, 'step59-consult-result-synced.png') })
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
