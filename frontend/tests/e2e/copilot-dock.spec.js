import { test, expect } from '@playwright/test'

// 右侧「法律助手」（copilot 面板）联调回归：
//   案件咨询页渲染 + 上下文条 → 收起/展开 → Enter 提交（POST body 带 question + case_id）
//   → 四层可解释性（过程时间线事后回放 / 证据卡 / 逐句溯源 / 忠实度）→ 复制答案
//   → 三条降级路径（后端不可达 / 形状 A 引用无 source_id / 无 faithfulness 字段）→ 接口 500 重试。
// 全 mock，不依赖后端与真实模型：token 直接写 localStorage（同 agent-flow / tasks-flow 写法）。

const success = (data) => ({ success: true, data })

const ME = { id: 15, username: 'pilot01-lawyer', role: 'user', organization_id: 5, department_id: null, status: 'active' }
const CASE = {
  id: 3, title: '张三劳动合同纠纷', case_type: 'labor_dispute', status: 'in_progress',
  description: '试用期辞退争议', owner_name: '王律师', client_name: '张三', opposing_party: '甲公司',
  updated_at: '2026-08-06T09:00:00', created_at: '2026-08-01T09:00:00',
  item_counts: { consultations: 1, contract_reviews: 0, drafts: 0 },
}

const QUESTION = '试用期最长多久？被辞退经济补偿怎么算？'
const ADVICE = '三年以上固定期限合同试用期不超过六个月；违法解除按经济补偿标准的二倍支付赔偿金。'
// 基线响应形状：引用带 source_id 但**没有** verification 字段、**没有** faithfulness 字段
// （对应后端未接法源库 / 未跑忠实度校验时的真实返回），面板必须显示「核验状态未知 / 忠实度未校验」。
const answer = (overrides = {}) => ({
  id: 77, case_id: 3, question: QUESTION, category: 'labor_dispute', known_facts: [], missing_facts: [],
  advice: ADVICE, risk_level: 'medium', status: 'draft', confidence: 82, feedback_score: null,
  created_at: '2026-08-07T10:00:00',
  references: [
    { source_id: 19, title: '劳动合同法 第十九条', citation: '劳动合同期限三年以上…试用期不得超过六个月。', version: '2012修正', status: 'active', effective_date: '2013-07-01' },
    { source_id: 87, title: '劳动合同法 第八十七条', citation: '违法解除或终止劳动合同的，按经济补偿标准的二倍支付赔偿金。', version: '2012修正', status: 'active', effective_date: '2013-07-01' },
  ],
  ...overrides,
})

// 案件工作台 + 全局外壳所需接口全量 mock；onConsult 控制 POST /legal/consultations 的行为
// onFaithfulness 控制生成后的忠实度补判：默认返回「判官不可用」，让未校验口径保持可断言。
// onConsultSocket 控制 WS 实时通道：默认直接关闭 → 面板回退到被 mock 的 REST 路径（旧断言不变）；
// 传了它则按 docs/websocket-protocol.md 的 consult 契约应答（覆盖 B1 实时时间线）。
async function mockApi(page, { onConsult, onFaithfulness, onConsultSocket } = {}) {
  const requests = []
  const failed = []
  const consultPosts = []
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) failed.push(`${r.request().method()} ${new URL(r.url()).pathname} -> ${r.status()}`)
  })
  await page.addInitScript(() => localStorage.setItem('token', 'copilot-e2e-token'))

  await page.routeWebSocket(/\/api\/ws\/chat/, (ws) => {
    if (onConsultSocket) return onConsultSocket(ws)
    ws.close()
  })

  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const path = url.pathname.replace(/^\/api/, '')
    const method = route.request().method()
    requests.push(`${method} ${path}${url.search ? `?${url.searchParams.toString()}` : ''}`)

    if (method === 'GET' && path === '/auth/me') return route.fulfill({ json: success(ME) })
    if (method === 'GET' && path === '/developer/notifications/me') return route.fulfill({ json: success({ items: [], unread: 0 }) })
    if (method === 'GET' && path === '/legal/overview') return route.fulfill({ json: success({ organization_id: 5, organization_name: '示例律所' }) })
    if (method === 'GET' && path === '/legal/history-counts') return route.fulfill({ json: success({ consultations: 0, contract_reviews: 0, drafts: 0 }) })
    if (method === 'GET' && path === '/legal/orgs/5/cases') return route.fulfill({ json: success([CASE]) })
    if (method === 'GET' && path === '/legal/orgs/5/cases/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/review-queue') return route.fulfill({ json: success({ items: [], total: 0 }) })
    if (method === 'GET' && path === '/legal/matters/3/items') return route.fulfill({ json: success({ consultations: [], contract_reviews: [], drafts: [] }) })
    if (method === 'GET' && path === '/legal/matters/3/activity') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/legal/consultations') return route.fulfill({ json: success([]) })
    if (method === 'GET' && path === '/billing/subscriptions/quota') return route.fulfill({ json: success(null) })
    if (method === 'GET' && path === '/legal/sources/19/articles') return route.fulfill({ json: success([{ id: 1901, article_number: '第十九条', title: '试用期', content: '劳动合同期限三年以上固定期限和无固定期限的，试用期不得超过六个月。' }]) })
    if (method === 'POST' && path === '/legal/consultations/77/feedback') return route.fulfill({ json: success({ ok: true }) })
    if (method === 'POST' && /^\/legal\/consultations\/\d+\/faithfulness$/.test(path)) {
      if (onFaithfulness) return onFaithfulness(route)
      return route.fulfill({
        json: success({ available: false, faithful: null, unsupported: [], reason: 'faithfulness_unavailable' }),
      })
    }
    if (method === 'POST' && path === '/legal/consultations') {
      const body = route.request().postDataJSON()
      consultPosts.push(body)
      return onConsult(route, body, consultPosts.length)
    }
    return route.fulfill({ status: 500, json: { detail: `Unhandled: ${method} ${path}` } })
  })

  return { requests, failed, consultPosts }
}

const dockSelector = '.copilot-dock'

const ask = async (page, dock) => {
  await dock.locator('.cop-textarea').fill(QUESTION)
  await dock.locator('.cop-textarea').press('Enter')
}

test('案件咨询页渲染面板：上下文条、空态、收起/展开', async ({ page }) => {
  const { failed } = await mockApi(page, { onConsult: (route) => route.fulfill({ json: success(answer()) }) })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await expect(dock).toBeVisible()
  await expect(dock.locator('.cop-head-text')).toContainText('法律助手')

  // 上下文条：当前模块（法律咨询）+ 当前案件（来自 URL 的 case_id，未额外请求案件详情）
  const ctx = dock.locator('.ctx')
  await expect(ctx).toContainText('法律咨询')
  await expect(ctx).toContainText('案件 #3')

  // 未提问 → 明确空态，不显示任何伪造的答复内容
  await expect(dock.locator('.cop-empty')).toContainText('尚未提问')
  await expect(dock.locator('.cop-answer')).toHaveCount(0)
  await expect(dock.locator('.cop-evidence')).toHaveCount(0)

  // 空输入不提交
  await expect(dock.locator('.cop-send')).toBeDisabled()
  await dock.locator('.cop-textarea').fill('   ')
  await expect(dock.locator('.cop-send')).toBeDisabled()
  await dock.locator('.cop-textarea').fill('')

  // 收起：面板只剩 52px 边缘条，主体与输入区不再渲染；展开后恢复
  expect(Math.round((await dock.boundingBox()).width)).toBeGreaterThanOrEqual(360)
  await page.getByRole('button', { name: '收起法律助手' }).click()
  await expect(dock).toHaveClass(/collapsed/)
  await expect(dock.locator('.cop-body')).toHaveCount(0)
  await expect(dock.locator('.cop-textarea')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '展开法律助手' })).toBeVisible()
  // 栅格列宽有 300ms 过渡，等它收敛后再量
  await expect.poll(async () => Math.round((await dock.boundingBox()).width)).toBeLessThanOrEqual(52)
  await page.getByRole('button', { name: '展开法律助手' }).click()
  await expect(dock.locator('.cop-body')).toBeVisible()
  await expect(dock.locator('.cop-textarea')).toBeVisible()
  await expect.poll(async () => Math.round((await dock.boundingBox()).width)).toBeGreaterThanOrEqual(360)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('Enter 提交：POST body 带 question + case_id，渲染 advice / 风险 / 置信度 / 证据卡', async ({ page }) => {
  const { requests, failed, consultPosts } = await mockApi(page, {
    // 模拟同步长请求（LLM 生成），便于断言"生成中"阶段文案
    onConsult: async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500))
      return route.fulfill({ json: success(answer()) })
    },
  })
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // 生成中：真实阶段文案（时间维度提示，非伪造步骤）+ 口径说明 + 禁止重复提交
  await expect(dock.locator('.cop-progress')).toContainText('正在检索相关法源…')
  await expect(dock.locator('.cop-progress-note')).toContainText('没有实时步骤事件')
  await expect(dock.getByRole('button', { name: '生成中…' })).toBeDisabled()
  await expect(dock.locator('.cop-textarea')).toBeDisabled()

  // 请求契约：POST /legal/consultations 且 body 带 question 与 case_id
  await expect.poll(() => consultPosts.length).toBe(1)
  expect(consultPosts[0]).toEqual({ question: QUESTION, case_id: 3 })
  expect(requests).toContain('POST /legal/consultations')

  // 结果区：提问原文气泡 → 建议正文 → 风险/分类/置信度 → 证据卡
  await expect(dock.locator('.cop-question')).toHaveText(QUESTION)
  await expect(dock.locator('.cop-answer')).toContainText('试用期不超过六个月')
  await expect(dock.locator('.cop-tag', { hasText: '中风险' })).toBeVisible()
  await expect(dock.locator('.cop-tag', { hasText: '劳动争议' })).toBeVisible()
  await expect(dock.locator('.cop-tag', { hasText: '置信度 82%' })).toBeVisible()
  await expect(dock.locator('.cop-evidence', { hasText: '劳动合同法 第十九条' })).toBeVisible()
  await expect(dock.locator('.cop-evidence', { hasText: '劳动合同法 第八十七条' })).toBeVisible()
  // 成功后输入框清空，且不再处于生成中
  await expect(dock.locator('.cop-textarea')).toHaveValue('')
  await expect(dock.locator('.cop-progress')).toHaveCount(0)

  // 复制答案
  await dock.getByRole('button', { name: '复制答案' }).click()
  await expect(dock.getByRole('button', { name: '已复制' })).toBeVisible()
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain('试用期不超过六个月')

  // 反馈：POST 到该咨询的 feedback 接口，回来后回填 feedback_score（组件内展示"已收到反馈"）
  await dock.getByRole('button', { name: '有帮助' }).click()
  await expect(dock.getByText('反馈已提交，感谢您的评价。')).toBeVisible()
  await expect(dock.getByText('已收到反馈：有帮助')).toBeVisible()
  expect(requests).toContain('POST /legal/consultations/77/feedback')

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('实时通道：WS 逐帧推进度，面板边生成边显示服务端步骤', async ({ page }) => {
  const streamed = []
  const { requests, failed } = await mockApi(page, {
    // REST 也准备好：若面板没有走 WS，这个分支会被命中，测试随即可从请求记录里看出来
    onConsult: (route) => route.fulfill({ json: success(answer()) }),
    onConsultSocket: (ws) => {
      ws.onMessage((raw) => {
        const message = JSON.parse(raw)
        if (message.type !== 'consult') return
        streamed.push(message)
        ws.send(JSON.stringify({ type: 'welcome', session_id: 1, resume_token: 'rt', last_seq: 0, resumed: false }))
        ws.send(JSON.stringify({ type: 'consult_step', id: 'retrieve', label: '检索法源', status: 'success', duration_ms: 12, detail: '带回 2 条候选法源' }))
        ws.send(JSON.stringify({ type: 'consult_step', id: 'generate', label: '生成建议', status: 'success', duration_ms: 900, detail: '模型返回结构化结果' }))
        // 留住「生成中」窗口，才能断言实时步骤确实渲染过
        setTimeout(() => {
          ws.send(JSON.stringify({ type: 'consult_done', consultation: answer() }))
        }, 800)
      })
    },
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // 实时步骤：逐条来自服务端帧，状态与耗时照实展示
  await expect(dock.locator('.cop-live-step')).toHaveCount(2)
  await expect(dock.locator('.cop-live-step').first()).toContainText('检索法源')
  await expect(dock.locator('.cop-live-step').first()).toContainText('完成')
  await expect(dock.locator('.cop-live-step').first()).toContainText('12ms')
  await expect(dock.locator('.cop-live-step').first()).toContainText('带回 2 条候选法源')
  await expect(dock.locator('.cop-live-step').nth(1)).toContainText('生成建议')
  await expect(dock.locator('.cop-progress-note')).toContainText('实时推送')

  // 请求契约：走 WS（content + case_id），且没有回退到 REST
  await expect.poll(() => streamed.length).toBe(1)
  expect(streamed[0]).toEqual({ type: 'consult', content: QUESTION, case_id: 3 })
  expect(requests).not.toContain('POST /legal/consultations')

  // 结果帧到达：实时块让位给正式答复（trace 来自响应本身）
  await expect(dock.locator('.cop-answer')).toContainText('试用期不超过六个月')
  await expect(dock.locator('.cop-live-step')).toHaveCount(0)
  await expect(dock.locator('.cop-textarea')).toHaveValue('')

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('实时通道不可用：静默回退 REST，不显示任何伪造的实时步骤', async ({ page }) => {
  const { requests, failed } = await mockApi(page, {
    onConsult: (route) => route.fulfill({ json: success(answer()) }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  await expect(dock.locator('.cop-answer')).toContainText('试用期不超过六个月')
  expect(requests).toContain('POST /legal/consultations')
  await expect(dock.locator('.cop-live-step')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('证据卡可点开：走共享法源链路并渲染真实条文', async ({ page }) => {
  const { requests, failed } = await mockApi(page, { onConsult: (route) => route.fulfill({ json: success(answer()) }) })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  const card = dock.locator('.cop-evidence', { hasText: '劳动合同法 第十九条' })
  // 卡片头部即标题/来源/版本/核验状态的投影
  await expect(card.locator('.cop-evidence-meta')).toContainText('法源库 #19')
  await expect(card.locator('.cop-evidence-meta')).toContainText('版本 2012修正')
  await expect(card.locator('.cop-evidence-head')).toHaveAttribute('aria-expanded', 'false')

  await card.locator('.cop-evidence-head').click()
  await expect(card).toHaveClass(/is-open/)
  await expect(card.locator('.cop-evidence-head')).toHaveAttribute('aria-expanded', 'true')
  // 条文来自 GET /legal/sources/19/articles（真实链路，不是响应里的 citation 复读）
  await expect(card.locator('.cop-articles')).toContainText('试用期不得超过六个月')
  expect(requests).toContain('GET /legal/sources/19/articles')

  // 再点一次收起
  await card.locator('.cop-evidence-head').click()
  await expect(card).not.toHaveClass(/is-open/)
  await expect(card.locator('.cop-articles')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('四层可解释性：过程事后回放 / 逐句溯源退化 / 忠实度未校验', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            advice: '三年以上固定期限合同试用期不超过六个月；\n违法解除按经济补偿标准的二倍支付赔偿金。',
            known_facts: ['三年以上固定期限合同'],
            missing_facts: ['入职具体日期'],
            model_snapshot: { model: 'gpt-4o-mini', action: 'legal_consultation', generated_at: '2026-08-07T10:00:00', prompt_version: '3' },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // ① 过程时间线：口径必须自曝是「事后回放」，不是实时流；步骤由响应字段重建
  const trace = dock.locator('.cop-trace')
  await expect(trace.locator('.cop-mode')).toHaveText('事后回放')
  await expect(trace).toContainText('不是服务端的实时事件流')
  await expect(trace.locator('.cop-step')).toHaveCount(4)
  await expect(trace.locator('.cop-step', { hasText: '检索法源' })).toContainText('带回 2 条法源引用')
  await expect(trace.locator('.cop-step', { hasText: '生成建议' })).toContainText('模型 gpt-4o-mini')
  await expect(trace.locator('.cop-step', { hasText: '生成建议' })).toContainText('提示词 v3')
  await expect(trace.locator('.cop-step', { hasText: '抽取事实' })).toContainText('已确认 1 项 · 待补充 1 项')
  // 引用没有 verification 字段（第三种态）→ 不冒充已核验，单独计数
  await expect(trace.locator('.cop-step', { hasText: '核验引用' })).toContainText('2 条无核验字段')

  // ② 逐句溯源：退化粒度必须写在界面上；原文换行保留为两段
  const sentences = dock.locator('.cop-sentence')
  await expect(sentences).toHaveCount(2)
  await expect(dock.locator('.cop-line')).toHaveCount(2)
  await expect(dock.locator('.cop-note', { hasText: '粒度：整篇答案' })).toContainText('点任意一句可定位支撑它的证据')
  // 退化说明常显，不必点开才交代
  await expect(dock.locator('.cop-note', { hasText: '后端未返回逐句引用映射' })).toContainText('不是该句的专属依据')
  await expect(dock.locator('.cop-link-note')).toHaveCount(0)

  // 点第 1 句 → 该句高亮 + 全部证据高亮并说明这是整篇粒度，不是该句专属依据
  await sentences.first().click()
  await expect(sentences.first()).toHaveClass(/is-active/)
  await expect(sentences.first()).toHaveAttribute('aria-pressed', 'true')
  await expect(dock.locator('.cop-link-note')).toContainText('第 1 句')
  await expect(dock.locator('.cop-link-note')).toContainText('2 条证据')
  await expect(dock.locator('.cop-evidence.is-highlighted')).toHaveCount(2)

  // 再点一次取消选择
  await sentences.first().click()
  await expect(dock.locator('.cop-sentence.is-active')).toHaveCount(0)
  await expect(dock.locator('.cop-link-note')).toHaveCount(0)
  await expect(dock.locator('.cop-evidence.is-highlighted')).toHaveCount(0)

  // ④ 忠实度：响应没有 faithfulness 字段 → 明确「未校验」，不能显示通过
  await expect(dock.locator('.cop-faithful-unchecked')).toContainText('忠实度未校验')
  await expect(dock.locator('.cop-faithful-unchecked')).toContainText('后端本次未运行忠实度校验')
  await expect(dock.locator('.cop-faithful-pass')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('服务端 trace：时间线展示真实步骤与耗时，不再标「事后回放」', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            // 后端 trace_json 的真实形状（legal_service.consultation_payload 记录，序列化时透出）
            trace: {
              trace_version: 1,
              source: 'legal_consultation',
              steps: [
                { id: 'retrieve', label: '检索法源', status: 'success', duration_ms: 4, detail: '带回 2 条候选法源' },
                { id: 'generate', label: '生成建议', status: 'success', duration_ms: 2410, detail: '模型返回结构化结果 · 模型 glm-5.2 · 提示词 v3' },
                { id: 'facts', label: '抽取事实', status: 'success', duration_ms: 0, detail: '已确认 1 项 · 待补充 1 项' },
                { id: 'verify', label: '核验引用', status: 'skipped', duration_ms: 2, detail: '无法源引用，无需核验' },
              ],
            },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  const trace = dock.locator('.cop-trace')
  // 口径换成服务端步骤，且不能再出现「事后回放」字样
  await expect(trace.locator('.cop-mode')).toHaveText('服务端步骤')
  await expect(trace).toContainText('由服务端在处理过程中记录')
  await expect(trace).not.toContainText('事后回放')
  await expect(trace.locator('.cop-step')).toHaveCount(4)

  // 步骤文案直读服务端，不再由前端按响应字段重建
  await expect(trace.locator('.cop-step', { hasText: '检索法源' })).toContainText('带回 2 条候选法源')
  await expect(trace.locator('.cop-step', { hasText: '生成建议' })).toContainText('模型 glm-5.2')

  // 状态与耗时只在服务端 trace 里出现
  await expect(trace.locator('.cop-step', { hasText: '检索法源' }).locator('.cop-step-status')).toHaveText('完成')
  await expect(trace.locator('.cop-step', { hasText: '检索法源' }).locator('.cop-step-ms')).toHaveText('4ms')
  await expect(trace.locator('.cop-step', { hasText: '生成建议' }).locator('.cop-step-ms')).toHaveText('2.4s')
  await expect(trace.locator('.cop-step', { hasText: '核验引用' }).locator('.cop-step-status')).toHaveText('跳过')
  // 0 耗时不显示成「0ms」
  await expect(trace.locator('.cop-step', { hasText: '抽取事实' }).locator('.cop-step-ms')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('服务端 trace 形状异常时回退事后回放，不渲染编造的步骤', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            model_snapshot: { model: 'gpt-4o-mini', prompt_version: '3' },
            trace: { steps: [null] },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  const trace = dock.locator('.cop-trace')
  await expect(trace.locator('.cop-mode')).toHaveText('事后回放')
  await expect(trace.locator('.cop-step')).toHaveCount(4)
  await expect(trace.locator('.cop-step-status')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('降级：形状 A 引用（无 source_id）+ 忠实度未通过，如实标注不可核验', async ({ page }) => {
  const { requests, failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            references: [
              {
                title: '劳动合同法 第十九条',
                citation: '试用期不得超过六个月。',
                version: '2012修正',
                verification: { verified: false, verification_note: '未关联法源库条目，无法核验' },
              },
            ],
            faithfulness: { available: true, faithful: false, unsupported: ['“赔偿金按三倍计算”未在证据中找到支撑'], reason: '1 处断言未被证据支持' },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // 证据卡：来源显示为「未关联法源库条目」，核验态是明确的「未核验」而不是留白
  const card = dock.locator('.cop-evidence')
  await expect(card).toHaveCount(1)
  await expect(card.locator('.cop-evidence-meta')).toContainText('未关联法源库条目')
  await expect(card.locator('.cop-verify-unverified')).toHaveText('未核验')

  await card.locator('.cop-evidence-head').click()
  await expect(card.locator('.cop-verify-note-unverified')).toContainText('未关联法源库条目，无法核验')
  await expect(card.locator('.cop-evidence-body')).toContainText('该引用未带法源库条目 ID，打不开条文原文。')
  // 没有 source_id 就不该去打法源库接口
  expect(requests.filter((item) => item.includes('/legal/sources/'))).toEqual([])

  // 过程回放按真实核验结果计数
  await expect(dock.locator('.cop-step', { hasText: '核验引用' })).toContainText('1 条未核验')

  // 忠实度：后端说跑了且没通过 → 显示未通过 + 未被支持的断言
  await expect(dock.locator('.cop-faithful-fail')).toContainText('忠实度校验未通过')
  await expect(dock.locator('.cop-faithful-list')).toContainText('“赔偿金按三倍计算”未在证据中找到支撑')
  await expect(dock.locator('.cop-faithful-unchecked')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('降级：后端不可达 → 明确错误态与重试，不空白不崩', async ({ page }) => {
  const { failed, consultPosts } = await mockApi(page, {
    onConsult: (route) => route.abort('connectionrefused'),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  await expect.poll(() => consultPosts.length).toBe(1)
  // 网络层失败：给出统一错误态 + 重试入口，而不是空态或一直转圈
  const errorState = dock.locator('.qs-error')
  await expect(errorState).toBeVisible()
  await expect(errorState.getByRole('button', { name: '重试' })).toBeVisible()
  await expect(dock.locator('.cop-progress')).toHaveCount(0)
  await expect(dock.locator('.cop-empty')).toHaveCount(0)
  await expect(dock.locator('.cop-answer')).toHaveCount(0)
  // 提问仍在输入框里，重试不需要重新输入
  await expect(dock.locator('.cop-textarea')).toHaveValue(QUESTION)
  await expect(dock.locator('.cop-textarea')).toBeEnabled()

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('接口 500：错误态 + 重试（重试后恢复成功答复）', async ({ page }) => {
  const { failed, consultPosts } = await mockApi(page, {
    onConsult: (route, body, attempt) => {
      if (attempt === 1) return route.fulfill({ status: 500, json: { detail: '内部错误', error: { code: 'INTERNAL_SERVER_ERROR' } } })
      return route.fulfill({ json: success(answer({ question: body.question })) })
    },
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await dock.locator('.cop-textarea').fill(QUESTION)
  await dock.getByRole('button', { name: '提问' }).click()

  // 失败 → QueryStateView 错误态：文案来自 normalizeError（错误码映射），带重试按钮
  const errorState = dock.locator('.qs-error')
  await expect(errorState).toBeVisible()
  await expect(errorState).toContainText('服务器内部错误，请稍后重试')
  await expect(dock.locator('.cop-answer')).toHaveCount(0)

  // 重试：同一问题再次提交（输入框内容未被清空），这次成功 → 渲染答复
  await errorState.getByRole('button', { name: '重试' }).click()
  await expect(dock.locator('.cop-answer')).toContainText('试用期不超过六个月')
  await expect(dock.locator('.qs-error')).toHaveCount(0)
  expect(consultPosts.length).toBe(2)
  expect(consultPosts[1]).toEqual({ question: QUESTION, case_id: 3 })
  expect(failed).toEqual(['POST /api/legal/consultations -> 500'])
})

test('无案件上下文：模块名照旧，空态说明未绑定案件', async ({ page }) => {
  const { failed } = await mockApi(page, { onConsult: (route) => route.fulfill({ json: success(answer()) }) })

  await page.goto('/legal-workspace?view=cases')
  const dock = page.locator(dockSelector)
  await expect(dock.locator('.ctx')).toContainText('案件台账')
  await expect(dock.locator('.ctx')).not.toContainText('案件 #')
  await expect(dock.locator('.cop-empty')).toContainText('尚未绑定案件')
  await expect(dock.locator('.cop-empty')).toContainText('答复不会关联到具体案件')
  // 仍可提问（不是禁用态）
  await expect(dock.locator('.cop-textarea')).toBeEnabled()

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('窄屏：面板改为浮层，正文不再被 380px 面板挤窄', async ({ page }) => {
  const { failed } = await mockApi(page, { onConsult: (route) => route.fulfill({ json: success(answer()) }) })

  // 9xx 是被旧栅格挤得最窄的区间（768-1024：旧规则固定预留 68px + 380px）
  await page.setViewportSize({ width: 900, height: 800 })
  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await expect(dock).toBeVisible()
  expect(await dock.evaluate((el) => getComputedStyle(el).position)).toBe('fixed')
  expect(Math.round((await dock.boundingBox()).width)).toBeLessThanOrEqual(380)
  // 面板离开栅格流：正文列接近整宽（修复前只剩约 900-68-380=452px）
  expect(Math.round((await page.locator('.main-content').boundingBox()).width)).toBeGreaterThan(700)

  // 375px 手机：浮层不超出可视宽；收起后只留 52px 展开条，且仍能点开
  await page.setViewportSize({ width: 375, height: 720 })
  await expect.poll(async () => Math.round((await dock.boundingBox()).width)).toBeLessThanOrEqual(375)
  await page.getByRole('button', { name: '收起法律助手' }).click()
  await expect.poll(async () => Math.round((await dock.boundingBox()).width)).toBeLessThanOrEqual(52)
  await page.getByRole('button', { name: '展开法律助手' }).click()
  await expect(dock.locator('.cop-textarea')).toBeVisible()

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

// 服务端句级引用映射（attribution_json，0098 迁移）：ADVICE 两句，服务端只给第 1 句匹配到依据
const SERVER_ATTRIBUTION = {
  granularity: 'sentence',
  attribution_version: 1,
  method: 'lexical_overlap',
  min_score: 0.34,
  total_sentences: 2,
  matched_sentences: 1,
  sentences: [
    {
      index: 0,
      text: '三年以上固定期限合同试用期不超过六个月；',
      matches: [
        { reference_index: 0, source_id: 19, title: '劳动合同法 第十九条', score: 0.58, shared_terms: ['试用期', '六个月'] },
      ],
    },
    { index: 1, text: '违法解除按经济补偿标准的二倍支付赔偿金。', matches: [] },
  ],
}

test('服务端句级映射：点某句只高亮该句真正的依据，匹配不上的句子如实留空', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) => route.fulfill({ json: success(answer({ attribution: SERVER_ATTRIBUTION })) }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // 粒度换成逐句，并且不再显示「整篇答案」的退化说明
  await expect(dock.locator('.cop-note', { hasText: '粒度：逐句' })).toContainText('1/2 句匹配到依据')
  await expect(dock.locator('.cop-note', { hasText: '粒度：整篇答案' })).toHaveCount(0)
  await expect(dock.locator('.cop-note', { hasText: '未返回逐句引用映射' })).toHaveCount(0)
  await expect(dock.locator('.cop-sentence')).toHaveCount(2)

  // 第 1 句 → 只有它自己的那条依据高亮（不再把 2 条证据全点亮）
  await dock.locator('.cop-sentence').first().click()
  await expect(dock.locator('.cop-link-note')).toContainText('第 1 句')
  await expect(dock.locator('.cop-link-note')).toContainText('劳动合同法 第十九条')
  await expect(dock.locator('.cop-evidence.is-highlighted')).toHaveCount(1)
  await expect(dock.locator('.cop-evidence.is-highlighted')).toContainText('劳动合同法 第十九条')

  // 第 2 句 → 服务端没匹配到依据，如实说明且不高亮任何证据
  await dock.locator('.cop-sentence').nth(1).click()
  await expect(dock.locator('.cop-link-note')).toContainText('第 2 句')
  await expect(dock.locator('.cop-link-note')).toContainText('没有匹配到依据')
  await expect(dock.locator('.cop-evidence.is-highlighted')).toHaveCount(0)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

test('服务端句级映射与本地分句对不齐时回退整篇粒度，不把句子指到错的证据', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            attribution: {
              ...SERVER_ATTRIBUTION,
              sentences: [
                { index: 0, text: '这句文本与服务端分句不一致；', matches: [{ reference_index: 0, score: 0.9 }] },
                { index: 1, text: '违法解除按经济补偿标准的二倍支付赔偿金。', matches: [] },
              ],
            },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  await expect(dock.locator('.cop-note', { hasText: '粒度：整篇答案' })).toContainText('点任意一句可定位支撑它的证据')
  await expect(dock.locator('.cop-note', { hasText: '与本地分句对不齐' })).toContainText('已回退')
  await dock.locator('.cop-sentence').first().click()
  await expect(dock.locator('.cop-evidence.is-highlighted')).toHaveCount(2)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

// 证据可核验性（0100 迁移）：生成时的确定性统计，随响应返回；老记录没有该字段就不渲染
test('证据可核验性：响应带 evidence 时在元信息行给出依据标签，缺字段时不渲染', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) =>
      route.fulfill({
        json: success(
          answer({
            evidence: {
              version: 1,
              level: 'partial',
              label: '部分引用已核对',
              references: 2,
              article_checked: 2,
              article_verified: 1,
              article_missing: 1,
              no_corpus: 0,
              no_numbers: 0,
              unmatched: 0,
              materials: 1,
              note: '1 条引用已与法源条文核对一致；1 条引用的条号未在已收录条文中找到；已把 1 份案件材料纳入证据',
            },
          }),
        ),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  const chip = dock.locator('.cop-meta .cop-tag', { hasText: '依据：部分引用已核对' })
  await expect(chip).toHaveCount(1)
  await expect(chip).toHaveClass(/cop-tag-neutral/)
  // 口径细节挂在 title 上：不只给结论，用户能看到「几条核到、几条没核到」
  await expect(chip).toHaveAttribute('title', /1 条引用的条号未在已收录条文中找到/)
  await expect(chip).toHaveAttribute('title', /已把 1 份案件材料纳入证据/)

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

// 反向断言：基线响应（没有 evidence 字段，等同老记录）不得凭空出现依据标签
test('证据可核验性：响应没有 evidence 字段时不渲染该标签', async ({ page }) => {
  const { failed } = await mockApi(page, {
    onConsult: (route) => route.fulfill({ json: success(answer()) }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  await expect(dock.locator('.cop-meta .cop-tag', { hasText: '依据：' })).toHaveCount(0)
  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})

// 忠实度补判（POST /legal/consultations/{id}/faithfulness）：答案先渲染，判官结论回来再翻转标记
test('生成后补判忠实度：标记从校验中翻成结论，且不改动答案本身', async ({ page }) => {
  const { failed, requests } = await mockApi(page, {
    onConsult: (route) => route.fulfill({ json: success(answer()) }),
    onFaithfulness: (route) =>
      route.fulfill({
        json: success({
          available: true,
          faithful: false,
          unsupported: ['「赔偿金按二倍计算」未在证据中找到依据'],
          reason: '1 处断言未被证据支持。',
          evidence_count: 2,
          checked_at: '2026-10-19T02:00:00+00:00',
        }),
      }),
  })

  await page.goto('/legal-workspace?case_id=3&tab=consultation')
  const dock = page.locator(dockSelector)
  await ask(page, dock)

  // 判官结论落地：标记翻转、未支持断言列出，答案正文仍在
  await expect(dock.locator('.cop-faithful')).toHaveClass(/cop-faithful-fail/)
  await expect(dock.locator('.cop-faithful-label')).toHaveText('忠实度校验未通过')
  await expect(dock.locator('.cop-faithful-list li')).toHaveCount(1)
  await expect(dock.locator('.cop-answer')).toContainText('违法解除按经济补偿标准的二倍支付赔偿金。')
  expect(requests).toContain('POST /legal/consultations/77/faithfulness')

  await expect(page.locator('#runtime-error-panel')).toHaveCount(0)
  expect(failed).toEqual([])
})
