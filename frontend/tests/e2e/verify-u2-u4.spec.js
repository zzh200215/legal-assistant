/**
 * 临时验证脚本 — U-2 信任三件套 + U-4 端侧反馈入口（浏览器实测）
 *
 * 前置：后端 API 在 http://127.0.0.1:8001 运行（vite 代理），数据库含 demo_lawyer。
 * 咨询创建接口被 mock（避免依赖 LLM），反馈提交被拦截断言 payload；
 * 法源详情弹窗走真实后端（验证 schema 修复后的 /sources/{id}/articles）。
 *
 * 运行：E2E_RUN_INTEGRATION=true npx playwright test verify-u2-u4 --workers=1
 *
 * 依赖真实后端（localhost:8001）与数据库 demo_lawyer 账号，故默认跳过；
 * 需先启动后端，并设置 E2E_RUN_INTEGRATION=true 才执行。
 */
import { test, expect } from '@playwright/test'

test.skip(!process.env.E2E_RUN_INTEGRATION, 'Set E2E_RUN_INTEGRATION=true to run against a running backend + prepared database')

const BASE_URL = 'http://localhost:5173'
const USERNAME = 'demo_lawyer'
const PASSWORD = 'Demo@123456'

const MOCK_CONSULT = {
  id: 99,
  category: 'labor_dispute',
  risk_level: 'low',
  known_facts: ['工作3年被辞退，公司未支付经济补偿'],
  missing_facts: [],
  references: [{
    source_id: 2,
    title: '中华人民共和国劳动合同法',
    citation: '第四十七条：经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资…',
    version: '2025-01-01',
    status: 'active',
    effective_date: '2008-01-01',
    jurisdiction: '全国',
  }],
  advice: '公司单方解除劳动合同应依法支付经济补偿。',
  status: 'needs_lawyer_review',
  confidence: 82,
  feedback_score: null,
}

async function login(page) {
  const resp = await page.request.post(`${BASE_URL}/api/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  })
  const token = (await resp.json()).data.access_token
  await page.addInitScript((t) => localStorage.setItem('token', t), token)
}

function mockConsultations(page) {
  return page.route('**/api/legal/consultations', (route) => {
    if (route.request().method() === 'POST') {
      route.fulfill({ json: { success: true, data: MOCK_CONSULT } })
    } else {
      route.fulfill({ json: [MOCK_CONSULT] })
    }
  })
}

async function submitConsult(page) {
  await page.fill('textarea[placeholder^="例如"]', '被公司辞退未支付经济补偿，该怎么办？')
  await page.click('button:has-text("提交咨询")')
  await expect(page.locator('text=咨询结果')).toBeVisible({ timeout: 8000 })
}

test('U-2: 咨询结果展示置信度标签 + 增强引用 + 法源详情弹窗', async ({ page }) => {
  await login(page)
  await mockConsultations(page)
  await page.goto(`${BASE_URL}/legal-workspace`)
  await submitConsult(page)

  // 置信度标签
  const confTag = page.locator('.result-header .el-tag:has-text("置信度")')
  await expect(confTag).toBeVisible()
  await expect(confTag).toHaveText('置信度 82%')

  // 可点击引用按钮 + 状态 tag + 版本
  await expect(page.locator('.ref-item .ref-title')).toHaveText('中华人民共和国劳动合同法')
  await expect(page.locator('.ref-item').locator('text=当前有效')).toBeVisible()
  await expect(page.locator('.ref-item .ref-version')).toHaveText('版本 2025-01-01')

  // 法源详情弹窗（走真实后端 /sources/2/articles）
  await page.click('.ref-item .ref-title')
  const dialog = page.locator('.el-dialog:has-text("引用依据核对")')
  await expect(dialog).toBeVisible({ timeout: 8000 })
  await expect(dialog.locator('text=中华人民共和国劳动合同法')).toBeVisible()
  await expect(dialog.locator('text=2008-01-01')).toBeVisible()
  await expect(dialog.locator('text=当前有效')).toBeVisible()
  await expect(dialog.locator('.article-list')).toBeVisible()
})

test('U-4: 「有帮助」提交 score=1 并更新反馈状态', async ({ page }) => {
  await login(page)
  await mockConsultations(page)
  let payload = null
  await page.route('**/api/legal/consultations/99/feedback', (route) => {
    payload = route.request().postDataJSON()
    route.fulfill({ json: { success: true, data: null } })
  })
  await page.goto(`${BASE_URL}/legal-workspace`)
  await submitConsult(page)

  await expect(page.locator('text=这个结果对您有帮助吗？')).toBeVisible()
  await page.click('button:has-text("有帮助")')
  await expect(page.locator('text=已收到反馈：有帮助')).toBeVisible({ timeout: 5000 })
  expect(payload).toEqual({ score: 1, note: null })
})

test('U-4: 「待改进」选原因+备注提交 score=-1 并更新状态', async ({ page }) => {
  await login(page)
  await mockConsultations(page)
  let payload = null
  await page.route('**/api/legal/consultations/99/feedback', (route) => {
    payload = route.request().postDataJSON()
    route.fulfill({ json: { success: true, data: null } })
  })
  await page.goto(`${BASE_URL}/legal-workspace`)
  await submitConsult(page)

  await page.click('button:has-text("待改进")')
  await page.locator('.feedback-panel .el-select').click()
  await page.locator('.el-select-dropdown__item:has-text("结论不准确")').click()
  await page.fill('.feedback-panel textarea', '结论与法条不符')
  await page.click('.feedback-panel button:has-text("提交反馈")')

  await expect(page.locator('text=已收到反馈：待改进')).toBeVisible({ timeout: 5000 })
  expect(payload.score).toBe(-1)
  expect(payload.note).toContain('结论不准确')
  expect(payload.note).toContain('结论与法条不符')
})
