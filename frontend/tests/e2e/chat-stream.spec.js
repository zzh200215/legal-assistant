import { test, expect } from '@playwright/test'

// Chat 流式对话回归：WebSocket 连接（bearer.{token} subprotocol）→ 发送 → chunk 逐字追加 → done 收尾。
// 用 routeWebSocket mock 服务端，锁定 WS 协议与前端流式渲染接线。
const success = (data) => ({ success: true, data })

test('对话 WebSocket：发送消息→流式 chunk→done 完成', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []

  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })

  await page.addInitScript(() => localStorage.setItem('token', 'chat-e2e-token'))
  await page.route('**/api/auth/me', (route) => {
    route.fulfill({ json: success({ id: 15, username: 'pilot01-lawyer', role: 'dept_admin', status: 'active' }) })
  })

  // 记忆偏好
  await page.route('**/api/memory/preferences**', (route) => {
    if (route.request().method() === 'GET') route.fulfill({ json: success([]) })
    else route.continue()
  })

  // 通知铃铛首屏加载（US-002）
  await page.route('**/api/developer/notifications/me', (route) => {
    route.fulfill({ json: success({ items: [], unread: 0 }) })
  })

  // WebSocket mock：流式返回
  await page.routeWebSocket('ws://localhost:8001/api/ws/chat', (route) => {
    route.onMessage((message) => {
      requests.wsMessage = JSON.parse(String(message))
      route.send(JSON.stringify({ type: 'session', session_id: 901 }))
      route.send(JSON.stringify({ type: 'chunk', content: '你好' }))
      route.send(JSON.stringify({ type: 'chunk', content: '，我是联调测试助手。' }))
      route.send(JSON.stringify({ type: 'done', content: '你好，我是联调测试助手。' }))
    })
  })

  await page.goto('/chat')
  await page.waitForTimeout(1200)
  const input = page.locator('textarea, input[type=text]').first()
  await input.fill('你好')
  await input.press('Enter')
  await page.waitForTimeout(1500)

  await expect(page.getByText('你好，我是联调测试助手。')).toBeVisible()
  await expect(page.getByText('已完成').first()).toBeVisible()
  expect(requests.wsMessage).toEqual({ content: '你好', session_id: null })

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
