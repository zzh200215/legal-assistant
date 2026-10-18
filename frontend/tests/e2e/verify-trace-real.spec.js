import { test, expect } from '@playwright/test'

// 真实后端集成核查：咨询过程 trace 是否真的落库并能经 REST 读回。
// 与 verify-u2-u4 的区别：这里不 mock 任何东西，也不触发新的 LLM 调用（只读已存在的记录），
// 因此不受模型额度/网关波动影响；库里暂时没有带 trace 的记录时按跳过处理并说明原因。
const RUN_INTEGRATION = process.env.E2E_RUN_INTEGRATION === 'true'
const API_BASE = process.env.E2E_API_BASE || 'http://127.0.0.1:8001'
const KNOWN_STATUS = ['success', 'degraded', 'skipped', 'refused']

test.describe('真实后端：咨询过程 trace', () => {
  test.skip(!RUN_INTEGRATION, '需 E2E_RUN_INTEGRATION=true 与真实后端（见 docs/review/step7-copilot-server-trace.md）')

  test('咨询记录持久化 trace 并可经 REST 读回（结构自洽）', async ({ request }) => {
    const username = process.env.E2E_ADMIN_USERNAME
    const password = process.env.E2E_ADMIN_PASSWORD
    expect(username, '需要 E2E_ADMIN_USERNAME').toBeTruthy()
    expect(password, '需要 E2E_ADMIN_PASSWORD').toBeTruthy()

    const login = await request.post(`${API_BASE}/api/auth/login`, { data: { username, password } })
    expect(login.ok()).toBeTruthy()
    const token = (await login.json()).data.access_token
    const headers = { Authorization: `Bearer ${token}` }

    const list = await request.get(`${API_BASE}/api/legal/consultations`, { headers })
    expect(list.ok()).toBeTruthy()
    const rows = (await list.json()).data || []
    const withTrace = rows.find((row) => Array.isArray(row.trace?.steps) && row.trace.steps.length > 0)
    test.skip(!withTrace, '当前库中没有带 trace 的咨询记录：先跑一次真实咨询（POST /api/legal/consultations）再执行本用例')

    // 列表与详情必须给出同一份 trace（证明它是落库读出的，而不是仅创建时返回）
    const detail = await request.get(`${API_BASE}/api/legal/consultations/${withTrace.id}`, { headers })
    expect(detail.ok()).toBeTruthy()
    const trace = (await detail.json()).data.trace
    expect(trace).toEqual(withTrace.trace)
    expect(trace.trace_version).toBe(1)
    expect(trace.source).toBe('legal_consultation')

    for (const step of trace.steps) {
      expect(typeof step.id).toBe('string')
      expect(step.label).toBeTruthy()
      expect(KNOWN_STATUS).toContain(step.status)
      expect(step.duration_ms).toBeGreaterThanOrEqual(0)
    }
    const ids = trace.steps.map((step) => step.id)
    // 拒答记录只有一步 refuse；正常/降级链路必须是固定四步且顺序稳定
    if (ids.length === 4) {
      expect(ids).toEqual(['retrieve', 'generate', 'facts', 'verify'])
    } else {
      expect(ids).toEqual(['refuse'])
    }
    // metrics 为后加字段：老记录没有，有则必须自洽（三态计数之和等于引用数，轮次如实为单跳）
    if (trace.metrics) {
      expect(trace.metrics.rounds).toBe(1)
      expect(trace.metrics.verified + trace.metrics.unverified + trace.metrics.unmarked).toBe(trace.metrics.references)
    }
  })
})
