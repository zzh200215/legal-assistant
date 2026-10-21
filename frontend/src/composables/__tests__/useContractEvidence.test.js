import test from 'node:test'
import assert from 'node:assert/strict'
import {
  EVIDENCE_CHAIN_LEVELS,
  buildContractEvidenceChain,
  chainFromServer,
  evidenceForRisk,
  evidenceLevel,
  normalizeEvidenceText,
  resolveEvidenceChain,
  resolveRiskEvidence,
  riskEvidenceKey,
} from '../useContractEvidence.js'

const CONTENT = '甲方：星河制造 乙方：云帆智能\n付款：签约后5日内付100万，阶段验收后付108万。\n违约：逾期交付按日0.3%支付违约金，上限为合同总额10%。'

const risk = ({ paragraph = 1, snippet = '', level = 'high', key = 'payment', description = '描述' } = {}) => ({
  clause_type: key,
  risk_level: level,
  description,
  source_location: { paragraph, snippet },
})

test('空清单：total 0、level none，不产生假的覆盖率', () => {
  const chain = buildContractEvidenceChain([], CONTENT)
  assert.equal(chain.total, 0)
  assert.equal(chain.anchored, 0)
  assert.equal(chain.locatedRate, 0)
  assert.equal(chain.level, 'none')
  assert.deepEqual(chain.gaps, [])
})

test('全部有定位 → strong', () => {
  const chain = buildContractEvidenceChain(
    [risk({ paragraph: 1, snippet: '阶段验收后付108万' }), risk({ paragraph: 2, snippet: '上限为合同总额10%' })],
    CONTENT,
  )
  assert.equal(chain.total, 2)
  assert.equal(chain.anchored, 2)
  assert.equal(chain.unanchored, 0)
  assert.equal(chain.locatedRate, 1)
  assert.equal(chain.level, 'strong')
  assert.equal(chain.verified, 2)
})

test('部分定位按比例分档：2/3 partial、1/3 weak', () => {
  const two = buildContractEvidenceChain(
    [risk({ paragraph: 1 }), risk({ paragraph: 2 }), risk({ paragraph: null })],
    CONTENT,
  )
  assert.equal(two.level, 'partial')

  const one = buildContractEvidenceChain(
    [risk({ paragraph: 1 }), risk({ paragraph: null }), risk({ paragraph: null })],
    CONTENT,
  )
  assert.equal(one.level, 'weak')
  assert.equal(one.unanchored, 2)

  // 1/2 是「一半有定位」，归 partial（琥珀）而不是 weak（红）
  const half = buildContractEvidenceChain([risk({ paragraph: 1 }), risk({ paragraph: null })], CONTENT)
  assert.equal(half.level, 'partial')
})

test('snippet 去掉空白后能匹配原文；匹配不到记 false；没有 snippet 记 null', () => {
  const normalized = normalizeEvidenceText(CONTENT)
  assert.equal(resolveRiskEvidence(risk({ snippet: '阶段验收后付 108 万' }), normalized).snippetVerified, true)
  assert.equal(resolveRiskEvidence(risk({ snippet: '合同里没有这句话' }), normalized).snippetVerified, false)
  assert.equal(resolveRiskEvidence(risk({ snippet: '' }), normalized).snippetVerified, null)
})

test('no snippet 不进 snippet 分母，避免把「没有片段」算成核对通过', () => {
  const chain = buildContractEvidenceChain(
    [risk({ paragraph: 1, snippet: '阶段验收后付108万' }), risk({ paragraph: 2, snippet: '' })],
    CONTENT,
  )
  assert.equal(chain.snippets, 1)
  assert.equal(chain.verified, 1)
  assert.equal(chain.snippetRate, 1)
})

test('gaps 只包含没有条款定位的风险', () => {
  const chain = buildContractEvidenceChain(
    [risk({ paragraph: 1 }), risk({ paragraph: null, description: '缺失条款' })],
    CONTENT,
  )
  assert.equal(chain.gaps.length, 1)
  assert.equal(chain.gaps[0].risk.description, '缺失条款')
})

test('evidenceForRisk 按稳定 key 命中，未知风险返回 null', () => {
  const anchored = risk({ paragraph: 2, snippet: '上限为合同总额10%' })
  const chain = buildContractEvidenceChain([anchored], CONTENT)
  assert.equal(evidenceForRisk(chain, anchored).anchored, true)
  assert.equal(evidenceForRisk(chain, risk({ paragraph: 9 })), null)
  assert.equal(evidenceForRisk(null, anchored), null)
})

test('riskEvidenceKey 对同一风险稳定、对不同风险可区分', () => {
  const a = risk({ paragraph: 1, snippet: 'A' })
  assert.equal(riskEvidenceKey(a), riskEvidenceKey({ ...a }))
  assert.notEqual(riskEvidenceKey(a), riskEvidenceKey(risk({ paragraph: 2, snippet: 'A' })))
  assert.equal(riskEvidenceKey({ id: 7 }), 'id:7')
})

test('证据链分档与文案齐备（徽标 hover 用）', () => {
  assert.equal(evidenceLevel(0, 0), 'none')
  assert.equal(evidenceLevel(0, 3), 'none')
  assert.equal(evidenceLevel(1, 3), 'weak')
  assert.equal(evidenceLevel(1, 2), 'partial')
  assert.equal(evidenceLevel(2, 3), 'partial')
  assert.equal(evidenceLevel(3, 3), 'strong')
  for (const level of ['strong', 'partial', 'weak', 'none']) {
    assert.ok(EVIDENCE_CHAIN_LEVELS[level].label, `${level} 缺少 label`)
    assert.ok(EVIDENCE_CHAIN_LEVELS[level].hint, `${level} 缺少 hint`)
  }
})

const serverRisk = (id, anchored, snippetVerified) => ({
  id,
  clause_type: 'payment',
  description: '服务端已判',
  source_location: { paragraph: anchored ? 1 : null, snippet: anchored ? '合同总金额为人民币一百万元' : '' },
  evidence: { anchored, paragraph: anchored ? 1 : null, snippet_verified: snippetVerified },
})

const SERVER_SUMMARY = {
  total: 2,
  anchored: 1,
  unanchored: 1,
  snippets: 1,
  verified: 1,
  located_rate: 0.5,
  snippet_rate: 1,
  level: 'partial',
}

test('服务端已下发 evidence 时按其口径装配（不再本地重算）', () => {
  const chain = chainFromServer([serverRisk(1, true, true), serverRisk(2, false, null)], SERVER_SUMMARY)
  assert.equal(chain.total, 2)
  assert.equal(chain.anchored, 1)
  assert.equal(chain.unanchored, 1)
  assert.equal(chain.level, 'partial')
  assert.equal(chain.source, 'server')
  assert.equal(chain.gaps.length, 1)
  assert.equal(evidenceForRisk(chain, { id: 1 }).anchored, true)
})

test('resolveEvidenceChain 优先服务端；缺 evidence 字段时回退本地', () => {
  const serverChain = resolveEvidenceChain([serverRisk(1, true, true), serverRisk(2, false, null)], SERVER_SUMMARY, CONTENT)
  assert.equal(serverChain.source, 'server')

  // e2e 夹具 / 老缓存：没有 evidence 字段与 summary
  const localChain = resolveEvidenceChain([risk({ paragraph: 1 })], null, CONTENT)
  assert.equal(localChain.source, 'client')
  assert.equal(localChain.anchored, 1)

  // 有条目缺 evidence 也回退，避免一半服务端一半本地混算
  const mixed = resolveEvidenceChain([serverRisk(1, true, true), risk({ paragraph: 1 })], SERVER_SUMMARY, CONTENT)
  assert.equal(mixed.source, 'client')
})
