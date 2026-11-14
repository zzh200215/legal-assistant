import { test, expect } from '@playwright/test'

// 文档中心上传入口：原生文件选择（替代 el-upload 后的回归保护）。
// 全 mock：只验证「选文件 → 状态更新 → 上传按钮可用」这条链路，不触发真实解析。
const success = (data) => ({ success: true, data })
const ME = { id: 15, username: 'doc-upload', role: 'dept_admin', organization_id: 5, department_id: 51, status: 'active' }

test('文档中心：选择文件后显示已选数量并可上传', async ({ page }) => {
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'doc-upload-token'))
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    if (path === '/auth/me') return route.fulfill({ json: success(ME) })
    return route.fulfill({
      json: success({ items: [], total: 0, list: [], knowledge_bases: [], documents: [] }),
    })
  })

  await page.goto('/documents')
  await expect(page.getByRole('heading', { name: '文档' })).toBeVisible()
  await expect(page.locator('.upload-dropzone')).toBeVisible()
  await expect(page.locator('.upload-dropzone-inner')).toContainText('拖拽文件到此处')
  await expect(page.locator('.upload-card .app-card-head-meta')).toContainText('文件不会自动上传')

  const fileInput = page.locator('.upload-dropzone input[type=file]')
  await fileInput.setInputFiles([
    { name: '合同草案.txt', mimeType: 'text/plain', buffer: Buffer.from('甲方乙方，合同正文。') },
    { name: '证据目录.txt', mimeType: 'text/plain', buffer: Buffer.from('证据一、证据二。') },
  ])

  await expect(page.locator('.upload-card .app-card-head-meta')).toContainText('已选 2 份')
  await expect(page.getByRole('button', { name: /上传并分析/ })).toBeEnabled()
  // 选完即清空 input.value：同一文件可以再次选择
  await expect(fileInput).toHaveJSProperty('value', '')

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
