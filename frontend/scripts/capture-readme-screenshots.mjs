/**
 * 生成 README 用的关键功能截图。
 *
 * 前置条件：
 *   1. 后端已启动（默认 http://127.0.0.1:8001）
 *   2. 前端 dev server 已启动（默认 http://127.0.0.1:5173）
 *   3. 已执行 `python scripts/seed_demo_data.py` 写入演示组织与演示数据
 *
 * 用法：
 *   cd frontend
 *   node scripts/capture-readme-screenshots.mjs
 *
 * 可选环境变量：
 *   README_SCREENSHOT_BASE_URL   前端地址，默认 http://127.0.0.1:5173
 *   README_SCREENSHOT_OUT        输出目录，默认 ../../docs/images
 *   README_SCREENSHOT_ACCOUNT    演示账号，默认 demo_lawyer
 *   README_SCREENSHOT_PASSWORD   演示口令，默认 Demo@123456
 *   README_SCREENSHOT_CDP        复用已启动的浏览器（如 http://127.0.0.1:9333），缺省则自行启动 Chromium
 *
 * 截图只读取本地演示数据，不会上传文件、不会触发模型调用。
 */
import { mkdir, readFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const scriptDir = resolve(fileURLToPath(new URL('.', import.meta.url)))
const baseUrl = (process.env.README_SCREENSHOT_BASE_URL || 'http://127.0.0.1:5173').replace(/\/$/, '')
const outputDir = resolve(scriptDir, process.env.README_SCREENSHOT_OUT || '../../docs/images')
const demoAccount = process.env.README_SCREENSHOT_ACCOUNT || 'demo_lawyer'
const demoPassword = process.env.README_SCREENSHOT_PASSWORD || 'Demo@123456'
const viewport = { width: 1440, height: 900 }

/** 管理员页面（管理中心 / 系统健康）需要管理员账号；从环境变量或仓库根目录 .env 读取，缺失时跳过。 */
async function readAdminCredentials() {
  const fromEnv = (name) => process.env[name]?.trim()
  let username = fromEnv('README_SCREENSHOT_ADMIN_ACCOUNT')
  let password = fromEnv('README_SCREENSHOT_ADMIN_PASSWORD')
  if (username && password) return { username, password }
  try {
    const env = await readFile(resolve(scriptDir, '../../.env'), 'utf8')
    const pick = (key) => (env.match(new RegExp(`^${key}=(.*)$`, 'm')) || [, ''])[1].trim()
    username = username || pick('ADMIN_USERNAME')
    password = password || pick('ADMIN_PASSWORD')
  } catch {
    return null
  }
  return username && password ? { username, password } : null
}

async function newSession(browser) {
  const context = await browser.newContext({ viewport, deviceScaleFactor: 1, locale: 'zh-CN' })
  const page = await context.newPage()
  page.on('pageerror', (error) => console.warn('  [pageerror]', String(error).slice(0, 160)))
  return { context, page }
}

async function signIn(page, account, password) {
  await page.goto(`${baseUrl}/login`, { waitUntil: 'networkidle' })
  await page.locator('.login-form input[placeholder="用户名"]').first().fill(account)
  await page.locator('.login-form input[placeholder="密码"]').first().fill(password)
  await page.locator('.login-form .submit-btn').first().click()
  await page.waitForURL(/legal-workspace/, { timeout: 30_000 })
  // 等“登录成功”提示条自然消失，避免截图带上 toast
  await page.waitForTimeout(3500)
}

async function capture(page, name, { settle = 1800 } = {}) {
  await page.waitForTimeout(settle)
  await page.screenshot({ path: resolve(outputDir, `${name}.png`) })
  console.log(`  ✓ ${name}.png`)
}

async function visit(page, path, name, options) {
  await page.goto(`${baseUrl}${path}`, { waitUntil: 'networkidle' }).catch(() => {})
  await capture(page, name, options)
}

await mkdir(outputDir, { recursive: true })

const browser = process.env.README_SCREENSHOT_CDP
  ? await chromium.connectOverCDP(process.env.README_SCREENSHOT_CDP)
  : await chromium.launch({ headless: true })

try {
  // ── 演示律师视角：工作台、案件工作区、审核、资料、Agent、任务 ──
  const lawyer = await newSession(browser)
  console.log(`→ 登录演示账号 ${demoAccount}`)
  await signIn(lawyer.page, demoAccount, demoPassword)

  await visit(lawyer.page, '/legal-workspace', 'workspace-home')

  await visit(lawyer.page, '/cases', 'case-list')
  const caseCard = lawyer.page.locator('.case-card').first()
  if (await caseCard.count()) {
    await caseCard.click()
    await capture(lawyer.page, 'matter-overview', { settle: 2000 })
    for (const [tab, name] of [
      ['法律咨询', 'matter-consultation'],
      ['合同审查', 'matter-contract-review'],
      ['文书', 'matter-draft'],
    ]) {
      const button = lawyer.page.locator('.legal-matter-tab', { hasText: tab }).first()
      if (!(await button.count())) continue
      await button.click()
      await capture(lawyer.page, name)
    }
  }

  await visit(lawyer.page, '/legal-workspace?view=review', 'review-queue')
  await visit(lawyer.page, '/legal-workspace?view=research', 'legal-sources')
  await visit(lawyer.page, '/agent', 'agent')
  await visit(lawyer.page, '/tasks', 'tasks')
  const tableToggle = lawyer.page.locator('.el-radio-button', { hasText: '表格' }).first()
  if (await tableToggle.count()) {
    await tableToggle.click()
    await capture(lawyer.page, 'tasks-table', { settle: 1200 })
  }
  await lawyer.context.close()

  // ── 登录页（保持未登录状态） ──
  const anonymous = await newSession(browser)
  await anonymous.page.goto(`${baseUrl}/login`, { waitUntil: 'networkidle' })
  await capture(anonymous.page, 'login', { settle: 800 })
  await anonymous.context.close()

  // ── 管理员视角：平台健康与治理 ──
  const admin = await readAdminCredentials()
  if (admin) {
    console.log(`→ 登录管理员账号 ${admin.username}`)
    const adminSession = await newSession(browser)
    await signIn(adminSession.page, admin.username, admin.password)
    await visit(adminSession.page, '/system', 'system-health')
    await adminSession.context.close()
  } else {
    console.log('… 未找到管理员账号，跳过 system-health.png')
  }

  console.log(`完成：${outputDir}`)
} finally {
  await browser.close()
}
