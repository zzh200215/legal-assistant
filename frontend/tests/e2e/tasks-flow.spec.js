import path from 'node:path'
import { test, expect } from '@playwright/test'

// 任务中心（/tasks）联调回归：
//   kanban 加载 + overview 计数 → 详情对话框 4 路并行（子任务/评论/日志/关联 Agent）
//   → owner 可编辑（评论 POST、协作 PUT、状态 PUT）/ 共享只读
//   → 新建任务 POST → scope 筛选 → 路由 taskId 深链。
// 全 mock，锁定 API 契约与 UI 接线。
const success = (data) => ({ success: true, data })
const SHOT_DIR = process.env.E2E_SHOT_DIR || ''
const ME = { id: 15, username: 'pilot01-lawyer', role: 'dept_admin', organization_id: 5, department_id: 51, status: 'active' }

const ownerTask = (overrides = {}) => ({
  id: 101, user_id: 15, organization_id: 5, department_id: 51,
  title: '起草合同初稿', description: '按模板输出首版', assignee: '小李',
  collaborators: [], status: 'todo', priority: 'high', progress: 20,
  due_date: '2026-08-20T10:00:00', source_type: 'document', source_id: 10, parent_id: null,
  created_at: '2026-08-06T09:00:00', updated_at: '2026-08-06T09:00:00', ...overrides,
})
const sharedTask = (overrides = {}) => ({
  id: 202, user_id: 99, organization_id: 5, department_id: 52,
  title: '共享任务-案件归档', description: '组织共享任务', assignee: '老王',
  collaborators: [], status: 'in_progress', priority: 'medium', progress: 50,
  due_date: '2026-08-10T10:00:00', source_type: 'meeting', source_id: null, parent_id: null,
  created_at: '2026-08-05T09:00:00', updated_at: '2026-08-05T09:00:00', ...overrides,
})

test('任务看板：加载→详情并行→评论/协作/状态变更', async ({ page }) => {
  const requests = []
  const failed = []
  let comments = [{ id: 1, task_id: 101, user_id: 15, content: '联调评论一', created_at: '2026-08-06T10:00:00' }]

  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    const qs = url.search ? url.search.replace(/^\?/, '') : ''
    requests.push(`${method} ${path}${qs ? `?${qs}` : ''}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') {
      const scope = url.searchParams.get('scope') || 'all'
      return route.fulfill({ json: success({ items: [ownerTask(), sharedTask()], total: 2, page: 1, page_size: 12, scope }) })
    }
    if (method === 'GET' && path === '/tasks/101') return route.fulfill({ json: success(ownerTask({ progress: 80, collaborators: ['张三', '李四'] })) })
    if (method === 'GET' && path === '/tasks/101/sub-tasks') return route.fulfill({ json: success({ items: [{ id: 1001, title: '子任务A', status: 'todo' }], total: 1 }) })
    if (method === 'GET' && path === '/tasks/101/comments') return route.fulfill({ json: success(comments) })
    if (method === 'GET' && path === '/tasks/101/logs') return route.fulfill({ json: success([{ id: 1, task_id: 101, action: 'task.created', detail: '创建任务', created_at: '2026-08-06T09:00:00' }]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [{ id: 501, goal: '完成合同审查', status: 'completed', created_at: '2026-08-06T11:00:00', total_steps: 3 }], total: 1 }) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'POST' && path === '/tasks/101/comments') {
      const body = route.request().postDataJSON()
      expect(body).toEqual({ content: '阻塞项：需要法务确认' })
      requests.commentPost = body
      comments = [...comments, { id: 2, task_id: 101, user_id: 15, content: body.content, created_at: '2026-08-07T10:00:00' }]
      return route.fulfill({ json: success(comments[comments.length - 1]) })
    }
    if (method === 'PUT' && path === '/tasks/101') {
      const body = route.request().postDataJSON()
      if ('status' in body) {
        requests.statusPut = body
        return route.fulfill({ json: success(ownerTask({ status: 'done', progress: 80, collaborators: ['张三', '李四'] })) })
      }
      requests.collabPut = body
      return route.fulfill({ json: success(ownerTask({ progress: 80, collaborators: ['张三', '李四'] })) })
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks')
  await expect(page.getByText('起草合同初稿')).toBeVisible()
  await expect(page.getByText('共享任务-案件归档')).toBeVisible()
  await expect(page.getByText('共享任务-案件归档')).toBeVisible()
  await expect(page.locator('.task-brief')).toContainText('2 项任务')
  // 初始列表请求带默认 scope=all 与分页参数
  expect(requests.some((r) => r.startsWith('GET /tasks/?page=1&page_size=12&scope=all'))).toBe(true)

  // 打开 owner 任务详情 → 4 路并行加载
  await page.locator('.kanban-task', { hasText: '起草合同初稿' }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('起草合同初稿').first()).toBeVisible()
  await expect(dialog.getByText('子任务A')).toBeVisible()
  await expect(dialog.getByText('联调评论一')).toBeVisible()
  await expect(dialog.getByText('完成合同审查')).toBeVisible()
  // 详情对话框 4 路并行 = 子任务/评论/日志（/tasks/{id}/…）+ 关联 Agent（/agent/runs）
  expect(requests.filter((r) => r.startsWith('GET /tasks/101/')).length).toBeGreaterThanOrEqual(3)
  expect(requests.some((r) => r.startsWith('GET /agent/runs?artifact_type=task&artifact_id=101'))).toBe(true)

  // owner 可编辑：添加评论
  await dialog.getByPlaceholder('记录进展、阻塞项或协作说明').fill('阻塞项：需要法务确认')
  await dialog.getByRole('button', { name: '添加评论' }).click()
  await expect(dialog.getByText('阻塞项：需要法务确认')).toBeVisible()
  expect(requests.commentPost).toEqual({ content: '阻塞项：需要法务确认' })

  // 协作信息更新 → PUT {progress, collaborators} + 重新拉取
  await dialog.getByPlaceholder('进度 %').fill('80')
  await dialog.getByPlaceholder('协作者，逗号分隔').fill('张三, 李四')
  await dialog.getByRole('button', { name: '保存协作信息' }).click()
  await expect(dialog.getByText('张三、李四')).toBeVisible()
  expect(requests.collabPut).toEqual({ progress: 80, collaborators: ['张三', '李四'] })

  // 状态流转 → PUT {status}
  await dialog.getByRole('button', { name: '标记完成' }).click()
  expect(requests.statusPut).toEqual({ status: 'done' })

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('共享任务只读 + 新建任务 + scope 筛选', async ({ page }) => {
  const requests = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    const qs = url.search ? url.search.replace(/^\?/, '') : ''
    requests.push(`${method} ${path}${qs ? `?${qs}` : ''}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') return route.fulfill({ json: success({ items: [ownerTask(), sharedTask()], total: 2, page: 1, page_size: 12 }) })
    if (method === 'GET' && path === '/tasks/202/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/202/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/202/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'POST' && path === '/tasks/') {
      requests.taskCreate = route.request().postDataJSON()
      return route.fulfill({ json: success(ownerTask({ id: 304, title: requests.taskCreate.title, status: 'todo' })) })
    }

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks')
  await expect(page.getByText('共享任务-案件归档')).toBeVisible()

  // 共享任务（非 owner）→ 只读：横幅 + 编辑按钮禁用
  await page.locator('.kanban-task', { hasText: '共享任务-案件归档' }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('共享任务当前仅支持查看，更新、评论和状态变更需由创建人操作')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '保存协作信息' })).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '添加评论' })).toBeDisabled()
  await expect(dialog.getByRole('button', { name: '标记完成' })).toBeDisabled()

  // 新建任务 → POST /tasks/（先 ESC 关闭详情对话框）
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '新建任务' }).click()
  await page.getByPlaceholder('任务标题').fill('联调新建任务')
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('创建成功')).toBeVisible()
  expect(requests.taskCreate).toEqual({ title: '联调新建任务', description: '', assignee: '', collaborators: [], priority: 'medium', progress: 0 })

  // scope 筛选 → 请求携带 scope=mine
  await page.locator('.el-select', { hasText: '全部可见' }).click()
  await page.locator('.el-select-dropdown:visible .el-select-dropdown__item:has-text("我的")').click()
  await page.waitForTimeout(400)
  expect(requests.some((r) => r.startsWith('GET /tasks/') && r.includes('scope=mine'))).toBe(true)

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('路由 taskId 深链：不在列表中的任务经 GET /tasks/{id} 拉取并打开详情', async ({ page }) => {
  const requests = []
  const failed = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') return route.fulfill({ json: success({ items: [ownerTask()], total: 1, page: 1, page_size: 12 }) })
    if (method === 'GET' && path === '/tasks/999') {
      requests.deepGet = true
      return route.fulfill({ json: success(ownerTask({ id: 999, title: '深链目标任务' })) })
    }
    if (method === 'GET' && path === '/tasks/999/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/999/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/999/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks?taskId=999')
  await expect(page.locator('.el-dialog:visible').getByText('深链目标任务').first()).toBeVisible()
  expect(requests.deepGet).toBe(true)

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('案件任务视图：case_id 查询参数只显示该案件任务，清除后恢复全部', async ({ page }) => {
  const requests = []
  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}?${url.searchParams.toString()}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') {
      const caseId = url.searchParams.get('case_id')
      const items = caseId
        ? [ownerTask({ id: 301, title: '案件专属任务', case_id: Number(caseId), case_title: '试点案件' })]
        : [ownerTask({ id: 101, title: '普通任务' }), ownerTask({ id: 302, title: '另一案件任务', case_id: 8 })]
      return route.fulfill({ json: success({ items, total: items.length, page: 1, page_size: 12 }) })
    }
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/legal/matters/7/summary') {
      requests.caseSummary = true
      return route.fulfill({ json: success({ case: { id: 7, title: '试点案件' } }) })
    }
    if (method === 'POST' && path === '/tasks/') {
      requests.taskCreate = route.request().postDataJSON()
      return route.fulfill({ json: success(ownerTask({ id: 305, title: requests.taskCreate.title, case_id: 7, case_title: '试点案件' })) })
    }
    if (method === 'GET' && path === '/tasks/301/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/301/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/301/logs') return route.fulfill({ json: success([]) })
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks?case_id=7')
  await expect(page.getByText('案件专属任务')).toBeVisible()
  await expect(page.locator('.kanban-task', { hasText: '案件专属任务' }).getByText('试点案件')).toBeVisible()
  await expect(page.getByText('普通任务')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '仅看案件 试点案件 ×' })).toBeVisible()
  expect(requests.caseSummary).toBe(true)
  expect(requests.some((item) => item.includes('/tasks/') && item.includes('case_id=7'))).toBe(true)

  // 详情面板案件入口：所属案件显示名称，点「打开案件」进案件工作台并带上 case_id
  await page.locator('.kanban-task', { hasText: '案件专属任务' }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('所属案件')).toBeVisible()
  await expect(dialog.getByText('试点案件')).toBeVisible()
  if (SHOT_DIR) {
    await page.setViewportSize({ width: 1280, height: 1100 })
    await page.screenshot({ path: path.join(SHOT_DIR, 'step81-task-detail-case.png') })
  }
  await dialog.getByRole('button', { name: '打开案件' }).click()
  await expect(page).toHaveURL(/\/legal-workspace\?case_id=7$/)

  // 案件工作台接口不在本用例 mock 范围，整页切回任务列表继续验证
  await page.goto('/tasks?case_id=7')
  await expect(page.getByRole('button', { name: '仅看案件 试点案件 ×' })).toBeVisible()

  // 案件视图内新建任务：默认归入当前案件，请求体带 case_id，创建后仍按案件筛选
  await page.getByRole('button', { name: '新建任务' }).click()
  const createDialog = page.locator('.el-dialog:visible')
  await expect(createDialog.getByText('创建后自动归入案件 #7')).toBeVisible()
  if (SHOT_DIR) await createDialog.screenshot({ path: path.join(SHOT_DIR, 'step82-task-create-in-case.png') })
  await createDialog.getByPlaceholder('任务标题').fill('案件内新建任务')
  await createDialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('创建成功')).toBeVisible()
  expect(requests.taskCreate).toEqual({
    title: '案件内新建任务', description: '', assignee: '', collaborators: [],
    priority: 'medium', progress: 0, case_id: 7,
  })
  expect(requests.filter((item) => item.startsWith('GET /tasks/') && item.includes('case_id=7')).length).toBeGreaterThanOrEqual(2)

  await page.getByRole('button', { name: '仅看案件 试点案件 ×' }).click()
  await expect(page.getByText('普通任务')).toBeVisible()
  await expect(page.getByRole('button', { name: '仅看案件 试点案件 ×' })).toHaveCount(0)
  expect(requests.some((item) => item.includes('/tasks/') && !item.includes('case_id='))).toBe(true)
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})

test('任务来源：咨询标签中文化 + 「查看来源咨询」深链', async ({ page }) => {
  const requests = []
  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') {
      const items = [
        ownerTask({ id: 401, title: '跟进劳动争议咨询', source_type: 'legal_consultation', source_id: 77, case_id: 7, case_title: '试点案件' }),
        ownerTask({ id: 402, title: '会议纪要待办', source_type: 'meeting', source_id: 3 }),
      ]
      return route.fulfill({ json: success({ items, total: items.length, page: 1, page_size: 12 }) })
    }
    if (method === 'GET' && path === '/tasks/401/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/401/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/401/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/matters/7/summary') return route.fulfill({ json: success({ case: { id: 7, title: '试点案件' } }) })
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks')
  const consultCard = page.locator('.kanban-task', { hasText: '跟进劳动争议咨询' }).first()
  await expect(consultCard).toBeVisible()
  // 看板来源标签：中文，不出现原始枚举
  await expect(consultCard.getByText('咨询', { exact: true })).toBeVisible()
  await expect(consultCard.getByText('legal_consultation')).toHaveCount(0)
  await expect(page.locator('.kanban-task', { hasText: '会议纪要待办' }).getByText('会议', { exact: true })).toBeVisible()

  await consultCard.click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('咨询 #77')).toBeVisible()
  await expect(dialog.getByRole('button', { name: '查看来源咨询' })).toBeVisible()
  if (SHOT_DIR) {
    await page.setViewportSize({ width: 1280, height: 1000 })
    await page.screenshot({ path: path.join(SHOT_DIR, 'step84-task-source-consultation.png') })
  }

  await dialog.getByRole('button', { name: '查看来源咨询' }).click()
  await expect(page).toHaveURL(/case_id=7/)
  await expect(page).toHaveURL(/tab=consultation/)
  await expect(page).toHaveURL(/consultation_id=77/)
})

test('任务截止日期：创建带日期 → 详情展示 → 修改/清除', async ({ page }) => {
  let currentDue = '2026-12-31T23:59:59'
  let currentProgress = 20
  let detailGets = 0
  const createBodies = []
  const putBodies = []

  await page.addInitScript(() => localStorage.setItem('token', 'tasks-e2e-token'))
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) { await route.continue(); return }
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    const dueTask = () => ownerTask({
      id: 501, title: '带截止日期任务', due_date: currentDue, progress: currentProgress, collaborators: [],
    })

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/tasks/') return route.fulfill({ json: success({ items: [dueTask()], total: 1, page: 1, page_size: 12 }) })
    if (method === 'GET' && path === '/tasks/501') {
      detailGets += 1
      return route.fulfill({ json: success(dueTask()) })
    }
    if (method === 'GET' && path === '/tasks/501/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/501/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/501/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/workflows') return route.fulfill({ json: success([]) })
    if (method === 'POST' && path === '/tasks/') {
      const body = route.request().postDataJSON()
      createBodies.push(body)
      return route.fulfill({ json: success(ownerTask({ id: 502, title: body.title, due_date: body.due_date })) })
    }
    if (method === 'PUT' && path === '/tasks/501') {
      const body = route.request().postDataJSON()
      putBodies.push(body)
      if ('due_date' in body) currentDue = body.due_date
      if (typeof body.progress === 'number') currentProgress = body.progress
      return route.fulfill({ json: success(dueTask()) })
    }
    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks')
  await expect(page.getByText('带截止日期任务')).toBeVisible()

  // 新建：日期只到天，提交时补 23:59:59，避免当天 00:00 就被判逾期
  await page.getByRole('button', { name: '新建任务' }).click()
  const createDialog = page.locator('.el-dialog:visible')
  await createDialog.getByPlaceholder('任务标题').fill('带期限的新任务')
  await createDialog.getByLabel('截止日期').fill('2026-12-31')
  await createDialog.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('创建成功')).toBeVisible()
  expect(createBodies[0]).toEqual({
    title: '带期限的新任务', description: '', assignee: '', collaborators: [],
    priority: 'medium', progress: 0, due_date: '2026-12-31T23:59:59',
  })

  // 详情：展示日期；不动日期只保存协作信息，不提交 due_date（避免改写原时间）
  await page.locator('.kanban-task', { hasText: '带截止日期任务' }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('2026-12-31')).toBeVisible()
  await dialog.getByRole('button', { name: '保存协作信息' }).click()
  await expect.poll(() => putBodies.length).toBe(1)
  expect(putBodies[0]).toEqual({ progress: 20, collaborators: [] })
  // 等保存后的详情回拉落定，避免它把刚填的日期重置回旧值
  await expect.poll(() => detailGets).toBeGreaterThanOrEqual(1)
  await expect(dialog.getByLabel('截止日期')).toHaveValue('2026-12-31')

  // 修改日期 → 提交当天 23:59:59，并回显新日期
  await dialog.getByLabel('截止日期').fill('2027-01-05')
  await expect(dialog.getByLabel('截止日期')).toHaveValue('2027-01-05')
  await dialog.getByRole('button', { name: '保存协作信息' }).click()
  await expect.poll(() => putBodies.length).toBe(2)
  expect(putBodies[1].due_date).toBe('2027-01-05T23:59:59')
  await expect(dialog.getByText('2027-01-05')).toBeVisible()

  // 清空日期 → 提交 null，回显「未设置」
  await dialog.getByLabel('截止日期').fill('')
  await dialog.getByRole('button', { name: '保存协作信息' }).click()
  await expect.poll(() => putBodies.length).toBe(3)
  expect(putBodies[2].due_date).toBeNull()
  await expect(dialog.getByText('未设置')).toBeVisible()
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
