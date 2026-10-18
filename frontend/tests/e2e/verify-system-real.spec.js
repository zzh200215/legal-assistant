// 真实后端集成核查：系统页（管理员）15 个 tab 逐一渲染。
// 默认跳过；显式开启方式：
//   $env:E2E_RUN_INTEGRATION='true'; $env:E2E_ADMIN_USERNAME='admin'; $env:E2E_ADMIN_PASSWORD='<密码>'
//   npx playwright test verify-system-real --workers=1
// 前置条件：本机后端在 127.0.0.1:8001（vite 代理目标），且存在管理员账号。
// 需要留存截图时追加 E2E_SHOT_DIR=.visual 会把每个 tab 存成 <dir>/real-<tab>.png。
import { test, expect } from '@playwright/test'

const RUN = process.env.E2E_RUN_INTEGRATION === 'true'
const USERNAME = process.env.E2E_ADMIN_USERNAME || ''
const PASSWORD = process.env.E2E_ADMIN_PASSWORD || ''
const SHOT_DIR = process.env.E2E_SHOT_DIR || ''

const TABS = [
  'health',
  'funnel',
  'event_funnel',
  'retention',
  'tokens',
  'oplogs',
  'alerts',
  'experiments',
  'feedback',
  'tool_health',
  'approvals',
  'knowledge',
  'orgs',
  'sensitivity',
  'tasks',
]

test.describe('真实后端：系统页管理员视图', () => {
  test.skip(!RUN, '需要 E2E_RUN_INTEGRATION=true')
  test.skip(!USERNAME || !PASSWORD, '需要 E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD')

  test('管理员登录后 15 个 tab 全部渲染且无运行时报错', async ({ page }) => {
    test.setTimeout(180_000)
    const pageErrors = []
    const failed = []
    page.on('pageerror', (err) => pageErrors.push(String(err)))
    page.on('response', (res) => {
      const url = res.url()
      if (url.includes('/api/') && res.status() >= 400) failed.push(`${res.status()} ${url}`)
    })

    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/login')
    // 登录页同时存在登录表单与注册表单，占位符重名 → 取第一个（登录表单）。
    await page.getByPlaceholder('用户名').first().fill(USERNAME)
    await page.getByPlaceholder('密码').first().fill(PASSWORD)
    await page.getByRole('button', { name: /^登录/ }).first().click()
    await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 })

    const failures = []
    for (const tab of TABS) {
      await page.goto(`/system?tab=${tab}`)
      await page.waitForLoadState('networkidle')
      const runtimeErrors = await page.locator('#runtime-error-panel').count()
      if (runtimeErrors > 0) {
        const text = await page.locator('#runtime-error-panel').innerText().catch(() => '')
        failures.push(`${tab}: 运行时错误面板可见 → ${text.slice(0, 200)}`)
      }
      if (SHOT_DIR) {
        await page.screenshot({ path: `${SHOT_DIR}/real-${tab}.png`, fullPage: true })
      }
    }

    expect(failures, failures.join('\n')).toEqual([])
    expect(pageErrors, pageErrors.join('\n')).toEqual([])
    // 真实后端下允许个别接口 4xx（如权限/空数据），但不应出现 5xx。
    const serverErrors = failed.filter((line) => line.startsWith('5'))
    expect(serverErrors, serverErrors.join('\n')).toEqual([])
  })
})
