import { test, expect } from '@playwright/test'

// 任务中心（/tasks）联调回归：
//   kanban 加载 + overview 计数 → 详情对话框 4 路并行（子任务/评论/日志/关联 Agent）
//   → owner 可编辑（评论 POST、协作 PUT、状态 PUT）/ 共享只读
//   → 新建任务 POST → scope 筛选 → 同步邮件 POST → 路由 taskId 深链。
// 全 mock，锁定 API 契约与 UI 接线。
const success = (data) => ({ success: true, data })
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
    if (method === 'GET' && path === '/tasks/') {
      const scope = url.searchParams.get('scope') || 'all'
      return route.fulfill({ json: success({ items: [ownerTask(), sharedTask()], total: 2, page: 1, page_size: 12, scope }) })
    }
    if (method === 'GET' && path === '/tasks/101') return route.fulfill({ json: success(ownerTask({ progress: 80, collaborators: ['张三', '李四'] })) })
    if (method === 'GET' && path === '/tasks/101/sub-tasks') return route.fulfill({ json: success({ items: [{ id: 1001, title: '子任务A', status: 'todo' }], total: 1 }) })
    if (method === 'GET' && path === '/tasks/101/comments') return route.fulfill({ json: success(comments) })
    if (method === 'GET' && path === '/tasks/101/logs') return route.fulfill({ json: success([{ id: 1, task_id: 101, action: 'task.created', detail: '创建任务', created_at: '2026-08-06T09:00:00' }]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [{ id: 501, goal: '完成合同审查', status: 'completed', created_at: '2026-08-06T11:00:00', total_steps: 3 }], total: 1 }) })
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
  await expect(page.getByText('任务总数')).toBeVisible()
  // 初始列表请求带默认 scope=all 与分页参数
  expect(requests.some((r) => r.startsWith('GET /tasks/?page=1&page_size=12&scope=all'))).toBe(true)

  // 打开 owner 任务详情 → 4 路并行加载
  await page.locator('.kanban-task', { hasText: '起草合同初稿' }).first().click()
  const dialog = page.locator('.el-dialog:visible')
  await expect(dialog.getByText('起草合同初稿').first()).toBeVisible()
  await expect(dialog.getByText('子任务A')).toBeVisible()
  await expect(dialog.getByText('联调评论一')).toBeVisible()
  await expect(dialog.getByText('完成合同审查')).toBeVisible()
  expect(requests.filter((r) => r.startsWith('GET /tasks/101/')).length).toBeGreaterThanOrEqual(4)
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

test('共享任务只读 + 新建任务 + scope 筛选 + 同步邮件', async ({ page }) => {
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
    if (method === 'GET' && path === '/tasks/') return route.fulfill({ json: success({ items: [ownerTask(), sharedTask()], total: 2, page: 1, page_size: 12 }) })
    if (method === 'GET' && path === '/tasks/202/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/202/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/202/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'POST' && path === '/tasks/') {
      requests.taskCreate = route.request().postDataJSON()
      return route.fulfill({ json: success(ownerTask({ id: 304, title: requests.taskCreate.title, status: 'todo' })) })
    }
    if (method === 'POST' && path === '/emails/from-tasks') {
      requests.syncEmail = route.request().postDataJSON()
      return route.fulfill({ json: success({ draft: { id: 42 }, subject_candidates: [] }) })
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

  // 同步邮件（含仅逾期变体）
  await page.getByRole('button', { name: '生成同步邮件' }).click()
  await expect(page.getByText(/已生成邮件草稿 #42/)).toBeVisible()
  expect(requests.syncEmail).toEqual({ include_overdue_only: false, purpose: '任务进度同步', tone: 'professional', need_action: true, scope: 'mine' })

  requests.syncEmail = null
  await page.getByRole('button', { name: '仅逾期任务邮件' }).click()
  await expect(page.getByText(/已生成邮件草稿 #42/)).toBeVisible()
  expect(requests.syncEmail).toEqual({ include_overdue_only: true, purpose: '逾期任务催办', tone: 'professional', need_action: true, scope: 'mine' })

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
    if (method === 'GET' && path === '/tasks/') return route.fulfill({ json: success({ items: [ownerTask()], total: 1, page: 1, page_size: 12 }) })
    if (method === 'GET' && path === '/tasks/999') {
      requests.deepGet = true
      return route.fulfill({ json: success(ownerTask({ id: 999, title: '深链目标任务' })) })
    }
    if (method === 'GET' && path === '/tasks/999/sub-tasks') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/tasks/999/comments') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/tasks/999/logs') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/agent/runs') return route.fulfill({ json: success({ items: [], total: 0 }) })

    await route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  await page.goto('/tasks?taskId=999')
  await expect(page.locator('.el-dialog:visible').getByText('深链目标任务').first()).toBeVisible()
  expect(requests.deepGet).toBe(true)

  expect(failed).toEqual([])
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
})
