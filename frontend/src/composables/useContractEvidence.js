/**
 * 合同审查「证据链」：把风险清单与合同原文对齐，算出可核验的条款定位情况。
 *
 * 口径与后端规则层（`scripts/run_contract_review_review.py` 的 `_norm` / 定位率）一致：
 * - **有定位** = `source_location.paragraph` 非空；
 * - **snippet 可核验** = 去掉空白后是合同原文的子串（防编造定位），与后端一样截前 80 字匹配；
 * - 没有 snippet 的风险不计入 snippet 分母（`snippetVerified = null`）。
 *
 * 为什么在前端也算一遍：审查结果落库后用户直接看这条链路，徽标必须与风险清单同源，
 * 而不是另存一份可能过期的统计。规则层读数（step21/step24）用于离线评测，这里用于当次展示。
 */

export const EVIDENCE_CHAIN_LEVELS = {
  strong: { label: '条款定位齐全', hint: '每条风险都能在合同原文定位到条款' },
  partial: { label: '部分风险有定位', hint: '还有风险没有条款定位，建议人工补齐' },
  weak: { label: '风险待定位', hint: '多数风险没有条款定位，定位结果仅供参考' },
  none: { label: '无条款定位', hint: '风险都没有定位到原文条款，建议人工逐条核对' },
}

const SNIPPET_MATCH_LIMIT = 80

export const normalizeEvidenceText = (text) => String(text ?? '').replace(/\s+/g, '')

export const riskEvidenceKey = (risk = {}) => {
  if (risk?.id !== undefined && risk?.id !== null) return `id:${risk.id}`
  const paragraph = risk?.source_location?.paragraph ?? 'none'
  const snippet = String(risk?.source_location?.snippet ?? '').trim()
  return `${risk?.clause_type || 'risk'}|${paragraph}|${snippet}|${risk?.description || ''}`
}

export function resolveRiskEvidence(risk = {}, normalizedContent = '') {
  const location = risk?.source_location && typeof risk.source_location === 'object' ? risk.source_location : {}
  const paragraph = location.paragraph ?? null
  const snippet = String(location.snippet ?? '').trim()
  const anchored = paragraph !== null && paragraph !== undefined && paragraph !== ''
  const snippetVerified = snippet
    ? normalizedContent.includes(normalizeEvidenceText(snippet).slice(0, SNIPPET_MATCH_LIMIT))
    : null
  const dismissed = String(risk?.status || '') === 'dismissed'
  return { anchored, paragraph, snippet, snippetVerified, dismissed }
}

export function evidenceLevel(anchored, total) {
  if (!total || anchored <= 0) return 'none'
  if (anchored >= total) return 'strong'
  return anchored / total >= 0.5 ? 'partial' : 'weak'
}

export function buildContractEvidenceChain(risks = [], content = '') {
  const normalizedContent = normalizeEvidenceText(content)
  const list = Array.isArray(risks) ? risks : []
  const items = list.map((risk) => ({ key: riskEvidenceKey(risk), risk, ...resolveRiskEvidence(risk, normalizedContent) }))
  // 已忽略的风险不算缺口，也不进覆盖率分母（与后端 annotate_contract_evidence 同口径）
  const activeItems = items.filter((item) => !item.dismissed)
  const total = activeItems.length
  const anchoredCount = activeItems.filter((item) => item.anchored).length
  const withSnippet = activeItems.filter((item) => item.snippet)
  const verified = withSnippet.filter((item) => item.snippetVerified).length

  return {
    total,
    anchored: anchoredCount,
    unanchored: total - anchoredCount,
    snippets: withSnippet.length,
    verified,
    locatedRate: total ? anchoredCount / total : 0,
    snippetRate: withSnippet.length ? verified / withSnippet.length : null,
    level: evidenceLevel(anchoredCount, total),
    items,
    gaps: activeItems.filter((item) => !item.anchored),
    byKey: new Map(items.map((item) => [item.key, item])),
  }
}

export function evidenceForRisk(chain, risk) {
  if (!chain) return null
  return chain.byKey?.get(riskEvidenceKey(risk)) || null
}

/**
 * 服务端已下发 evidence 时，直接用后端口径（每条风险带 `evidence`，顶层 `evidence_summary`）。
 * 保留本地计算只作为兼容回退：老缓存、e2e 夹具或第三方调用可能没有这两个字段。
 */
export function chainFromServer(risks = [], summary = {}) {
  const list = Array.isArray(risks) ? risks : []
  const items = list.map((risk) => {
    const evidence = risk?.evidence && typeof risk.evidence === 'object' ? risk.evidence : {}
    return {
      key: riskEvidenceKey(risk),
      risk,
      anchored: Boolean(evidence.anchored),
      paragraph: evidence.paragraph ?? risk?.source_location?.paragraph ?? null,
      snippet: String(risk?.source_location?.snippet ?? '').trim(),
      snippetVerified: evidence.snippet_verified ?? null,
      dismissed: String(risk?.status || '') === 'dismissed',
    }
  })
  // active = 去掉「已忽略」的风险；徽标分母用 active，避免被忽略的风险拉低覆盖率
  const total = summary?.active ?? summary?.total ?? items.length
  const anchored = summary?.anchored ?? items.filter((item) => item.anchored).length
  return {
    total,
    anchored,
    unanchored: summary?.unanchored ?? total - anchored,
    snippets: summary?.snippets ?? items.filter((item) => item.snippet).length,
    verified: summary?.verified ?? 0,
    locatedRate: summary?.located_rate ?? (total ? anchored / total : 0),
    snippetRate: summary?.snippet_rate ?? null,
    level: summary?.level || evidenceLevel(anchored, total),
    items,
    gaps: items.filter((item) => !item.anchored && !item.dismissed),
    byKey: new Map(items.map((item) => [item.key, item])),
    source: 'server',
  }
}

export function resolveEvidenceChain(risks = [], summary = null, content = '') {
  const list = Array.isArray(risks) ? risks : []
  if (summary && list.every((risk) => risk && risk.evidence)) {
    return chainFromServer(list, summary)
  }
  return { ...buildContractEvidenceChain(list, content), source: 'client' }
}

export function useContractEvidence() {
  return {
    buildContractEvidenceChain,
    chainFromServer,
    resolveEvidenceChain,
    evidenceForRisk,
    resolveRiskEvidence,
    riskEvidenceKey,
  }
}
