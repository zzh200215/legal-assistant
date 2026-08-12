import { test, expect } from '@playwright/test'

// System 反馈闭环处理 + 任务中心重试联调回归：
//   admin 挂载 → 负反馈 open 行「处理」→ POST /analytics/feedback/{id}/resolve
//   → 任务中心 retryable 行「重试」→ POST /analytics/task-runs/retry。
// 全 mock，锁定反馈处理与任务重试契约。
const success = (data) => ({ success: true, data })

const FEEDBACK = [{
  id: 5, document_title: '合同审查-联调', question: '试用期辞退是否合法', feedback_value: 'negative',
  feedback_status: 'open', feedback_reason: 'incorrect_answer', feedback_note: '引用不够新', feedback_resolution_note: null,
  feedback_created_at: '2026-08-06T10:00:00',
}]
const TASK_RUNS = [{
  source: 'async_task', task_key: 'doc-parse-10', title: '文档解析', status: 'failed', retryable: true,
  target_type: 'document', target_id: 10, message: '解析超时', task_run_id: 901, updated_at: '2026-08-06T11:00:00',
}]

test('System 反馈处理 + 任务重试', async ({ page }) => {
  const requests = {}
  const unexpected = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'system-tasks-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = request.method()

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success({ id: 3, username: 'admin', role: 'admin', status: 'active' }) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/health') return route.fulfill({ json: success({ status: 'ok', checks: { database: { status: 'ok' }, redis: { status: 'ok' }, llm_provider: { status: 'ok' } } }) })
    if (method === 'GET' && path === '/analytics/tokens/my-stats') return route.fulfill({ json: success({ by_action: {}, by_date: {}, by_model: {}, governance: { today: {}, rate_limit: {}, policy: {} } }) })
    if (method === 'GET' && path === '/analytics/tokens/global-stats') return route.fulfill({ json: success({ by_model: {}, governance: { today: {}, policy: {} } }) })
    if (method === 'GET' && path === '/analytics/llm-calls/stats') return route.fulfill({ json: success({ total_calls: 0, failed_calls: 0, avg_duration_ms: 0, success_rate: 0, by_module: {}, by_action: {}, failed_by_date: {} }) })
    if (method === 'GET' && path === '/analytics/llm-calls') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/llm-billing/stats') return route.fulfill({ json: success({ summary: {}, by_model: {}, pricing: { currency: 'CNY', items: [] } }) })
    if (method === 'GET' && path === '/analytics/llm-pricing') return route.fulfill({ json: success({ currency: 'CNY', items: [] }) })
    if (method === 'GET' && path === '/analytics/qa-replays') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/analytics/oplogs') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/oplogs/stats') return route.fulfill({ json: success({ total_operations: 0, by_module: {} }) })
    if (method === 'GET' && path === '/analytics/alerts') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/alerts/stats') return route.fulfill({ json: success({ total: 0, by_source: {}, by_category: {}, by_date: {} }) })
    if (method === 'GET' && path === '/analytics/tool-health') return route.fulfill({ json: success({ items: [] }) })
    if (method === 'GET' && path === '/analytics/experiments/overview') return route.fulfill({ json: success({ summary: {}, artifact_status: {}, rollouts: { items: [] }, experiments: [], prompt_traffic: { items: [] } }) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/documents/knowledge-bases') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/documents/') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/org/organizations') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/org/departments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/auth/users') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/admin/funnel') return route.fulfill({ json: success({ data: { funnel: [], cohort: { registered: 0 } } }) })
    if (method === 'GET' && path === '/admin/retention') return route.fulfill({ json: success({ data: {} }) })
    if (method === 'GET' && path === '/admin/north-star') return route.fulfill({ json: success({ data: {} }) })

    // 反馈
    if (method === 'GET' && path === '/analytics/feedback') return route.fulfill({ json: success({ items: FEEDBACK, total: 1, page: 1, page_size: 20 }) })
    if (method === 'GET' && path === '/analytics/feedback/stats') return route.fulfill({ json: success({ total_feedback: 1, positive_count: 0, open_count: 1, resolution_rate: 0, by_reason: { incorrect_answer: 1 }, by_date: {} }) })
    if (method === 'POST' && path === '/analytics/feedback/5/resolve') {
      requests.resolve = request.postDataJSON()
      return route.fulfill({ json: success({ ok: true }) })
    }
    // 任务中心
    if (method === 'GET' && path === '/analytics/task-runs') return route.fulfill({ json: success({ items: TASK_RUNS, total: 1, page: 1, page_size: 20 }) })
    if (method === 'POST' && path === '/analytics/task-runs/retry') {
      requests.retry = request.postDataJSON()
      return route.fulfill({ json: success({ source: 'async_task', task_key: 'doc-parse-10' }) })
    }

    unexpected.push(`${method} ${path}`)
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/system')

  // 反馈闭环：负反馈 open 行 → 处理 → resolve
  await page.getByRole('tab', { name: '反馈闭环', exact: true }).click()
  await expect(page.getByText('合同审查-联调').first()).toBeVisible()
  await page.getByRole('button', { name: '处理', exact: true }).click()
  const resolveDialog = page.locator('.el-dialog:visible')
  await resolveDialog.getByPlaceholder('填写处理结论').fill('已核实并更新引用版本')
  await resolveDialog.getByRole('button', { name: '确认处理', exact: true }).click()
  await expect(page.getByText('反馈已处理')).toBeVisible()
  await expect.poll(() => requests.resolve, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.resolve).toEqual({ resolution_note: '已核实并更新引用版本' })

  // 任务中心：retryable 行 → 重试 → retry POST
  await page.getByRole('tab', { name: '任务中心', exact: true }).click()
  await expect(page.getByText('文档解析').first()).toBeVisible()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByText('任务已重新提交')).toBeVisible()
  await expect.poll(() => requests.retry, { timeout: 5000 }).not.toBeUndefined()
  expect(requests.retry).toEqual({ source: 'async_task', task_key: 'doc-parse-10' })

  expect(unexpected).toEqual([])
  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
