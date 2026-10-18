import { defineConfig, devices } from '@playwright/test'

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
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:5173',
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
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 60000,
  },
})
