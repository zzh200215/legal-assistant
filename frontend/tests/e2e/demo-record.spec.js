/**
 * 5 分钟 demo 的「真实轨迹」录制器（离线演示兜底）。
 *
 * 用途：在真实后端上把 demo 主链路跑一遍，逐拍截图 + 录制视频；断网时用这些产物演示。
 * 口径：不 mock 任何接口（真登录、真咨询、真 LLM），因此会消耗配额、耗时数分钟。
 *
 * 运行（需要真后端 + 演示账号）：
 *   cd frontend
 *   $env:E2E_RUN_INTEGRATION='true'; $env:E2E_DEMO_USERNAME='demo_reviewer'; $env:E2E_DEMO_PASSWORD='Review@123456'
 *   npx playwright test demo-record --workers=1 --reporter=list --timeout=600000
 *
 * 产物：
 *   - 逐拍截图：`E2E_SHOT_DIR`（默认 `frontend/.visual/demo`）下的 `01-*.png` … `08-*.png`
 *   - 视频：Playwright 的 `test-results/**\/video.webm`（录制完成后由 runbook 复制到 `docs/review/demo/`）
 *
 * 前置数据（见 `docs/review/demo-5min.md` §0）：demo 账号有 1 个进行中案件、该案件下 1 份合同审查、
 * 至少 1 份文书草稿，且咨询配额未被用尽（否则第 3 拍会 429）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '@playwright/test'

const RUN = process.env.E2E_RUN_INTEGRATION === 'true'
const USERNAME = process.env.E2E_DEMO_USERNAME || 'demo_reviewer'
const PASSWORD = process.env.E2E_DEMO_PASSWORD || 'Review@123456'
const HERE = path.dirname(fileURLToPath(import.meta.url))
const SHOT_DIR = process.env.E2E_SHOT_DIR || path.join(HERE, '..', '..', '.visual', 'demo')

const CASE_QUESTION = '公司没有提前三十日通知就解除劳动合同，员工可以主张什么？'
const CONTRACT_QUESTION = '这份合同的付款期限是怎么约定的？'

test.use({ viewport: { width: 1280, height: 720 }, video: { mode: 'on', size: { width: 1280, height: 720 } } })

async function shot(page, name) {
  fs.mkdirSync(SHOT_DIR, { recursive: true })
  await page.screenshot({ path: path.join(SHOT_DIR, `${name}.png`), fullPage: false })
}

async function login(page) {
  await page.goto('/login')
  await page.getByPlaceholder('用户名').first().fill(USERNAME)
  await page.getByPlaceholder('密码').first().fill(PASSWORD)
  await page.getByRole('button', { name: /^登录/ }).first().click()
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 60_000 })
}

/** 在右栏助手提问并等到服务端 trace 渲染完成。 */
async function askDock(page, question) {
  const box = page.locator('.cop-textarea')
  await box.waitFor({ state: 'visible', timeout: 30_000 })
  await box.fill(question)
  await page.locator('.cop-send').click()
  await expect(page.locator('.cop-mode')).toContainText('服务端步骤', { timeout: 180_000 })
  await expect(page.locator('.cop-step')).toHaveCount(4, { timeout: 30_000 })
}

test.describe('5 分钟 demo 真实轨迹', () => {
  test.skip(!RUN, '需要真实后端：设 E2E_RUN_INTEGRATION=true')

  test('主链路逐拍录制', async ({ page }) => {
    test.setTimeout(600_000)

    // 拍 1：登录后的工作台首页（今天先处理什么 + 摘要卡 + 常用入口）
    await login(page)
    await page.goto('/legal-workspace')
    await expect(page.getByText('今天先处理什么？')).toBeVisible({ timeout: 60_000 })
    await shot(page, '01-workbench')

    // 拍 2：进入案件工作台（概览：案情摘要 / 最近活动 / 关键文档）
    await page.goto('/legal-workspace?case_id=4')
    await expect(page.getByText('劳动争议', { exact: false }).first()).toBeVisible({ timeout: 60_000 })
    await shot(page, '02-case-overview')

    // 拍 3：主链路——咨询提问，右栏显示服务端步骤时间线（真实耗时）
    await askDock(page, CASE_QUESTION)
    await expect(page.locator('.cop-refs')).toBeVisible({ timeout: 30_000 })
    await shot(page, '03-copilot-trace')

    // 拍 4：逐句溯源——点某一句，只高亮该句的依据
    const sentences = page.locator('.cop-sentence')
    const sentenceCount = await sentences.count()
    if (sentenceCount > 1) {
      await sentences.nth(1).click()
    } else if (sentenceCount === 1) {
      await sentences.first().click()
    }
    await shot(page, '04-sentence-attribution')

    // 拍 5：合同审查 tab——面板上下文条带上「当前：合同审查 · 案件 #N · 合同 #M」
    await page.goto('/legal-workspace?case_id=4&tab=contract')
    await expect(page.locator('.ctx')).toContainText('合同', { timeout: 60_000 })
    await shot(page, '05-contract-context')

    // 拍 6：基于当前合同的提问——答复引用合同里的付款条款
    await askDock(page, CONTRACT_QUESTION)
    await shot(page, '06-contract-answer')

    // 拍 7：文书 tab——上下文条带上当前文书
    await page.goto('/legal-workspace?case_id=4&tab=draft&draft_id=2')
    await expect(page.locator('.ctx')).toContainText('文书', { timeout: 60_000 })
    await shot(page, '07-draft-context')

    // 拍 8：面板头部「整页」→ 案件助手整页深度视图（保留的第三个入口）
    await page.locator('.cop-fullpage').click()
    await page.waitForURL(/\/chat/, { timeout: 60_000 })
    await shot(page, '08-full-page')
  })
})
