// 统一 query key 工厂：所有查询 key 都是可序列化数组，禁止在页面散落手写 key 字符串。
// 用途：
//  - useQuery({ key: qk.documents.list({ page, filters }) })
//  - useMutation 成功后 invalidate 相关 key（精确失效，不做全局暴力刷新）

export const qk = {  auth: {
    me: () => ['auth', 'me'],
  },
  documents: {
    list: (params) => ['documents', 'list', params],
    knowledgeBases: () => ['documents', 'knowledge-bases'],
    detail: (id) => ['documents', 'detail', id],
    versions: (id) => ['documents', id, 'versions'],
    parseJobs: (id) => ['documents', id, 'parse-jobs'],
    qaRecords: (id) => ['documents', id, 'qa-records'],
    relatedRuns: (id) => ['documents', id, 'related-runs'],
    analysis: (id) => ['documents', id, 'analysis'],
    conflictCases: () => ['documents', 'conflict-cases'],
  },
  agent: {
    runs: (params) => ['agent', 'runs', params],
    run: (id) => ['agent', 'runs', id],
    approvals: (params) => ['agent', 'approvals', params],
    metrics: (days) => ['agent', 'metrics', days],
    registry: () => ['agent', 'registry'],
  },
  legal: {
    overview: () => ['legal', 'overview'],
    cases: (orgId) => ['legal', 'cases', orgId],
    quota: () => ['billing', 'quota'],
    consultations: () => ['legal', 'consultations'],
    contractReviews: () => ['legal', 'contract-reviews'],
    drafts: () => ['legal', 'drafts'],
    sources: () => ['legal', 'sources'],
    reviewQueue: () => ['legal', 'review-queue'],
    reviewStats: () => ['legal', 'review-stats'],
    sourceArticles: (sourceId) => ['legal', 'sources', sourceId, 'articles'],
  },
  system: {
    health: () => ['system', 'health'],
    taskRuns: (params) => ['system', 'task-runs', params],
    approvals: (params) => ['system', 'approvals', params],
    notifications: () => ['developer', 'notifications', 'me'],
  },
}

/** 按 key 前缀匹配的失效谓词：invalidate: [qkPrefix('documents', 'list')] */
export function qkPrefix(...segments) {
  const prefix = JSON.stringify(segments).slice(0, -1)
  return (serializedKey) => serializedKey.startsWith(prefix)
}
