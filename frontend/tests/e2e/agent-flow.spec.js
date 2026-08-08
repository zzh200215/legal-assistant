import { test, expect } from '@playwright/test'

// Agent 工作台联调回归：
//   加载（registry/approvals/runs/metrics 4 路）→ WS run 执行 → 敏感操作审批
//   （run_waiting_approval → 弹窗 → 确认继续走 resume_approval WS）
//   → 拒绝走 REST decision → 取消执行 → 历史详情查看。
// 全 mock，锁定 WS 协议（/api/ws/agent, bearer.{token}）与 REST 契约。
const success = (data) => ({ success: true, data })

const ME = { id: 15, username: 'pilot01-lawyer', role: 'dept_admin', organization_id: 5, department_id: null, status: 'active' }

const REGISTRY = {
  supervisor: { agent_type: 'supervisor_agent', label: '法律总管 Agent', description: '负责编排', output_contract: '最终结论', execution_mode: 'orchestration_only', allowed_tools: [] },
  items: [{ agent_type: 'document_agent', label: '知识 Agent', description: '文档检索与总结', output_contract: '要点结论', execution_mode: 'tool_based', allowed_tools: ['document_search_tool', 'document_summary_tool'] }],
  registry_version: '1', protocol_version: '1',
}
const METRICS = { success_rate: 0.8, total_runs: 5, reliability: { tool_success_rate: 0.9, retrying_run_rate: 0.1, human_intervention_rate: 0.2 } }
const HISTORY = [{ id: 601, goal: '历史运行目标', status: 'completed', final_answer: '历史结果摘要', total_steps: 3, created_at: '2026-08-06T09:00:00', completed_at: '2026-08-06T09:05:00' }]
const RUN_DONE = {
  id: 502, user_id: 15, goal: '把敏感操作落地成任务', status: 'completed', result: null,
  final_answer: '已创建任务：敏感任务', failure_reason: null, error: null, total_steps: 2,
  artifacts: { tasks: [{ task_id: 1, title: '敏感任务', status: 'todo', priority: 'high' }] },
  supervisor_plan: { intent: '创建任务', risk_level: 'high', expected_artifacts: ['task'], workers: ['workflow_agent'], plan_source: 'llm' },
  created_at: '2026-08-07T10:00:00', completed_at: '2026-08-07T10:01:00',
}
const LOG1 = {
  id: 1, agent_run_id: 502, step: 1, action_type: 'tool_call', thought: '先创建任务', tool_name: 'task_create_tool',
  input_params: '{"title":"敏感任务"}', raw_decision: null, observation: null, output_result: 'ok',
  status: 'success', error: null, duration_ms: 320, created_at: '2026-08-07T10:00:00',
}

test('Agent 加载 4 路 + WS 执行完成渲染', async ({ page }) => {
  const requests = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'agent-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/agent/registry') return route.fulfill({ json: success(REGISTRY) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: HISTORY, total: 1, page: 1, page_size: 10 }) })
    if (method === 'GET' && path === '/agent/metrics') return route.fulfill({ json: success(METRICS) })
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  let wsRun = null
  await page.routeWebSocket('ws://localhost:8001/api/ws/agent', (route) => {
    route.onMessage((message) => {
      const msg = JSON.parse(String(message))
      if (msg.action === 'run') {
        wsRun = msg
        route.send(JSON.stringify({ type: 'run_started', run_id: 502, goal: msg.goal, status: 'running', created_at: '2026-08-07T10:00:00' }))
        route.send(JSON.stringify({ type: 'step_started', step: 1, action_type: 'tool_call', thought: '先创建任务', tool_name: 'task_create_tool', input_params: { title: '敏感任务' } }))
        route.send(JSON.stringify({ type: 'step_completed', log: LOG1 }))
        route.send(JSON.stringify({ type: 'run_completed', run: RUN_DONE }))
        route.send(JSON.stringify({ type: 'run_snapshot', run: RUN_DONE, logs: [LOG1] }))
      }
    })
  })

  await page.goto('/agent')
  await expect(page.getByText('企业专家协作网络')).toBeVisible()
  await expect(page.getByText('法律总管 Agent')).toBeVisible()
  await expect(page.getByText('历史运行目标')).toBeVisible()
  expect(requests).toContain('GET /agent/registry')
  expect(requests).toContain('GET /agent/approvals')
  expect(requests).toContain('GET /agent/runs')
  expect(requests).toContain('GET /agent/metrics')

  // 执行：填目标 → 直接执行 → WS 协议 + 结果渲染
  await page.locator('.input-card textarea').fill('把敏感操作落地成任务')
  await page.getByRole('button', { name: '直接执行' }).click()
  await expect(page.getByText('已创建任务：敏感任务').first()).toBeVisible()
  await expect(page.getByText('Agent 执行完成')).toBeVisible()
  expect(wsRun).toEqual({ action: 'run', goal: '把敏感操作落地成任务', max_steps: 5 })
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('敏感操作审批：等待确认 → 确认继续（resume_approval WS）→ 完成', async ({ page }) => {
  const requests = []
  const failed = []
  let approvals = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'agent-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/agent/registry') return route.fulfill({ json: success(REGISTRY) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success(approvals) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 10 }) })
    if (method === 'GET' && path === '/agent/metrics') return route.fulfill({ json: success(METRICS) })
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  let wsRun = null
  let wsResume = null
  await page.routeWebSocket('ws://localhost:8001/api/ws/agent', (route) => {
    route.onMessage((message) => {
      const msg = JSON.parse(String(message))
      if (msg.action === 'run') {
        wsRun = msg
        approvals = [{ id: 701, agent_run_id: 502, user_id: 15, tool_name: 'task_create_tool', agent_type: 'task_agent', input_params: '{"title":"敏感任务"}', risk_level: 'high', status: 'pending', approval_token: 'tok', decision_note: null, created_at: '2026-08-07T10:00:00' }]
        route.send(JSON.stringify({ type: 'run_started', run_id: 502, goal: msg.goal, status: 'running', created_at: '2026-08-07T10:00:00' }))
        route.send(JSON.stringify({ type: 'step_started', step: 1, action_type: 'tool_call', thought: '先创建任务', tool_name: 'task_create_tool', input_params: { title: '敏感任务' } }))
        route.send(JSON.stringify({ type: 'run_waiting_approval', run: { id: 502, goal: msg.goal, status: 'awaiting_approval', total_steps: 1, created_at: '2026-08-07T10:00:00' }, approval_request_id: 701 }))
      } else if (msg.action === 'resume_approval') {
        wsResume = msg
        route.send(JSON.stringify({ type: 'run_resumed', run_id: 502, status: 'running' }))
        route.send(JSON.stringify({ type: 'step_completed', log: LOG1 }))
        route.send(JSON.stringify({ type: 'run_completed', run: RUN_DONE }))
        route.send(JSON.stringify({ type: 'run_snapshot', run: RUN_DONE, logs: [LOG1] }))
      }
    })
  })

  await page.goto('/agent')
  await page.locator('.input-card textarea').fill('把敏感操作落地成任务')
  await page.getByRole('button', { name: '直接执行' }).click()

  // 敏感操作弹窗：run_waiting_approval → 审批列表待审批 + 弹窗
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('确认敏感操作')).toBeVisible()
  await expect(dialog.getByText('任务创建').first()).toBeVisible()
  await expect(page.getByText('待审批').first()).toBeVisible()

  // 确认并继续 → WS resume_approval
  await dialog.getByRole('button', { name: '确认并继续' }).click()
  await expect(page.getByText('已创建任务：敏感任务').first()).toBeVisible()
  expect(wsRun).toEqual({ action: 'run', goal: '把敏感操作落地成任务', max_steps: 5 })
  expect(wsResume).toEqual({ action: 'resume_approval', approval_id: 701 })
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('审批拒绝：REST decision 载荷 + 列表移除', async ({ page }) => {
  const requests = []
  const failed = []
  let approvals = [{
    id: 702, agent_run_id: 503, user_id: 15, tool_name: 'task_create_tool', agent_type: 'task_agent',
    input_params: '{"title":"待拒绝任务"}', risk_level: 'high', status: 'pending', approval_token: 'tok2',
    decision_note: null, created_at: '2026-08-07T10:00:00',
  }]
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'agent-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/agent/registry') return route.fulfill({ json: success(REGISTRY) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success(approvals) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0, page: 1, page_size: 10 }) })
    if (method === 'GET' && path === '/agent/metrics') return route.fulfill({ json: success(METRICS) })
    if (method === 'POST' && path === '/agent/approvals/702/decision') {
      requests.decision = route.request().postDataJSON()
      approvals = []
      return route.fulfill({ json: success({ ...requests.decision, id: 702, status: 'rejected' }) })
    }
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/agent')
  await expect(page.getByText('待审批').first()).toBeVisible()
  await page.getByRole('button', { name: '拒绝' }).click()
  await expect(page.getByText('审批已拒绝')).toBeVisible()
  await expect(page.getByText('暂无待审批操作')).toBeVisible()
  expect(requests.decision).toEqual({ approved: false, decision_note: '用户拒绝敏感操作' })
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('取消执行 + 历史详情查看', async ({ page }) => {
  const requests = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'agent-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)
    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/agent/registry') return route.fulfill({ json: success(REGISTRY) })
    if (method === 'GET' && path === '/agent/approvals') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: HISTORY, total: 1, page: 1, page_size: 10 }) })
    if (method === 'GET' && path === '/agent/metrics') return route.fulfill({ json: success(METRICS) })
    if (method === 'POST' && path === '/agent/runs/502/cancel') {
      requests.cancel = route.request().postDataJSON()
      return route.fulfill({ json: success({ id: 502, goal: '被取消的目标', status: 'cancelled', total_steps: 1 }) })
    }
    if (method === 'GET' && path === '/agent/runs/601') {
      requests.runDetail = true
      return route.fulfill({ json: success({ ...RUN_DONE, id: 601, goal: '历史运行目标', final_answer: '历史结果摘要', logs: [LOG1] }) })
    }
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.routeWebSocket('ws://localhost:8001/api/ws/agent', (route) => {
    route.onMessage((message) => {
      const msg = JSON.parse(String(message))
      if (msg.action === 'run') {
        route.send(JSON.stringify({ type: 'run_started', run_id: 502, goal: msg.goal, status: 'running', created_at: '2026-08-07T10:00:00' }))
        route.close()
      }
    })
  })

  await page.goto('/agent')

  // 取消执行：run_started 后 WS 关闭 → 状态 running → 取消按钮
  await page.locator('.input-card textarea').fill('将被取消的目标')
  await page.getByRole('button', { name: '直接执行' }).click()
  await page.getByRole('button', { name: '取消执行' }).click()
  await expect(page.getByText('执行已取消')).toBeVisible()
  expect(requests.cancel).toEqual({ reason: '用户在 Agent 工作台取消执行' })

  // 历史详情：查看 → GET /agent/runs/{id} → 渲染日志
  await page.getByRole('button', { name: '查看' }).click()
  await expect(page.getByText('历史结果摘要').first()).toBeVisible()
  await expect(page.getByText('步骤时间线').first()).toBeVisible()
  expect(requests.runDetail).toBe(true)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})
