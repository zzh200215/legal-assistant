import { defineConfig, devices } from '@playwright/test'

// e2e 专用端口：5173 是开发端口，容易和本机其它项目（或手动起的 dev server）撞车；
// 之前复用 5173 上「别的应用」导致整批用例假失败。这里固定 5174 且 strictPort，
// 起不来就直接报错，而不是悄悄复用别人的服务。
const TEST_PORT = process.env.PLAYWRIGHT_PORT || '5174'
const TEST_ORIGIN = `http://localhost:${TEST_PORT}`

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: 'list',
  // 开发态 Vite 是按需转换模块的：一台机器上并发跑「后端 + dev server」时，
  // 首次导航（`waiting until "load"` 要等整张模块图）偶发超过 30s 默认上限，
  // 表现为失败用例每次不同、重跑必过。这里把上限放宽，配合 vite.config.js 的
  // server.warmup 一起用；断言强度不变，只是不再把「服务器慢」当成产品缺陷。
  timeout: 60_000,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || TEST_ORIGIN,
    trace: 'on-first-retry',
    navigationTimeout: 45_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // Start dev server automatically when running tests
  webServer: {
    command: `npm run dev -- --port ${TEST_PORT} --strictPort`,
    url: TEST_ORIGIN,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
