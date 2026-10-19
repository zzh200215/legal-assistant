import test from 'node:test'
import assert from 'node:assert/strict'
import {
  deriveAttribution,
  deriveCopilotLayers,
  deriveEvidenceCards,
  deriveFaithfulness,
  deriveTraceReplay,
  deriveVerifiability,
  formatStepDuration,
  splitAnswerSentences,
} from '../copilotLayers.js'

// 形状 A（常见）：只有 title/citation/version/verification，没有 source_id
const SHAPE_A = {
  title: '劳动合同法 第十九条',
  citation: '三年以上固定期限合同试用期不得超过六个月。',
  version: '2012修正',
  verification: { verified: false, verification_note: '未关联法源库条目，无法核验' },
}

// 形状 B：法源库里有对应条目
const SHAPE_B = {
  source_id: 19,
  title: '劳动合同法 第十九条',
  citation: '三年以上固定期限合同试用期不得超过六个月。',
  version: '2012修正',
  status: 'active',
  effective_date: '2013-07-01',
  jurisdiction: 'CN',
  verification: {
    verified: true,
    status: 'active',
    current_effective: true,
    verification_note: '现行有效，可直接引用',
  },
}

const RESULT = {
  id: 77,
  advice: '试用期不得超过六个月。违法解除需支付赔偿金！',
  references: [SHAPE_A, SHAPE_B],
  known_facts: ['固定期限三年以上'],
  missing_facts: ['入职时间', '离职原因'],
  model_snapshot: {
    model: 'deepseek-chat',
    action: 'legal_consultation',
    generated_at: '2026-08-07T10:00:00+00:00',
    prompt_version: 3,
  },
}

test('splitAnswerSentences：按句末标点与换行切分，保留标点与顺序', () => {
  assert.deepEqual(splitAnswerSentences('第一句。第二句！第三句？'), ['第一句。', '第二句！', '第三句？'])
  assert.deepEqual(splitAnswerSentences('一；二\n三。'), ['一；', '二', '三。'])
  assert.deepEqual(splitAnswerSentences('结尾没有标点'), ['结尾没有标点'])
  assert.deepEqual(splitAnswerSentences('   '), [])
  assert.deepEqual(splitAnswerSentences(''), [])
  assert.deepEqual(splitAnswerSentences(null), [])
  assert.deepEqual(splitAnswerSentences(undefined), [])
  assert.deepEqual(splitAnswerSentences({}), [])
})

test('deriveEvidenceCards：形状 A 没有 source_id，标为不可打开条文', () => {
  const [card] = deriveEvidenceCards({ references: [SHAPE_A] })
  assert.equal(card.linkable, false)
  assert.equal(card.sourceId, null)
  assert.equal(card.title, '劳动合同法 第十九条')
  assert.equal(card.version, '2012修正')
  assert.equal(card.verification.state, 'unverified')
  assert.equal(card.verification.label, '未核验')
  assert.equal(card.verification.note, '未关联法源库条目，无法核验')
})

test('deriveEvidenceCards：形状 B 带 source_id/状态/生效日期，标为已核验', () => {
  const [card] = deriveEvidenceCards({ references: [SHAPE_B] })
  assert.equal(card.linkable, true)
  assert.equal(card.sourceId, 19)
  assert.equal(card.sourceStatus, 'active')
  assert.equal(card.effectiveDate, '2013-07-01')
  assert.equal(card.jurisdiction, 'CN')
  assert.equal(card.verification.state, 'verified')
  assert.equal(card.verification.note, '现行有效，可直接引用')
})

test('deriveEvidenceCards：没有 verification 字段时是「未知」，既不冒充已核验也不冒充未核验', () => {
  const [card] = deriveEvidenceCards({ references: [{ title: '某法' }] })
  assert.equal(card.verification.state, 'unknown')
  assert.equal(card.verification.note, '响应未返回核验结果，无法核验')
  assert.equal(card.linkable, false)
})

test('deriveEvidenceCards：无引用/字段类型异常时返回空数组而不是抛错', () => {
  assert.deepEqual(deriveEvidenceCards({}), [])
  assert.deepEqual(deriveEvidenceCards({ references: null }), [])
  assert.deepEqual(deriveEvidenceCards({ references: 'oops' }), [])
  assert.deepEqual(deriveEvidenceCards(undefined), [])
})

test('deriveFaithfulness：响应没有该字段 → 未校验，不当作通过', () => {
  const verdict = deriveFaithfulness({ advice: 'x' })
  assert.equal(verdict.state, 'unchecked')
  assert.equal(verdict.label, '忠实度未校验')
  assert.match(verdict.detail, /未运行忠实度校验/)
})

test('deriveFaithfulness：available=false 或 available 缺失都算未校验', () => {
  assert.equal(deriveFaithfulness({ faithfulness: { available: false } }).state, 'unchecked')
  assert.equal(deriveFaithfulness({ faithfulness: { faithful: true } }).state, 'unchecked')
  assert.equal(deriveFaithfulness({ faithfulness: null }).state, 'unchecked')
  assert.equal(deriveFaithfulness({ faithfulness: 'yes' }).state, 'unchecked')
})

test('deriveFaithfulness：available=true 但没有结论字段 → 仍不给结论', () => {
  const verdict = deriveFaithfulness({ faithfulness: { available: true } })
  assert.equal(verdict.state, 'unchecked')
  assert.match(verdict.detail, /没有结论字段/)
})

test('deriveFaithfulness：available=true 才按 faithful 布尔值给结论', () => {
  const pass = deriveFaithfulness({ faithfulness: { available: true, faithful: true, reason: '全部有据' } })
  assert.equal(pass.state, 'pass')
  assert.equal(pass.label, '忠实度校验通过')
  assert.equal(pass.detail, '全部有据')

  const fail = deriveFaithfulness({
    faithfulness: { available: true, faithful: false, unsupported: ['赔偿金倍数', ''] },
  })
  assert.equal(fail.state, 'fail')
  assert.deepEqual(fail.unsupported, ['赔偿金倍数'])
  assert.match(fail.detail, /1 处断言未被证据支持/)
})

test('deriveTraceReplay：每一步都给出响应里对应的字段依据', () => {
  const trace = deriveTraceReplay(RESULT)
  assert.equal(trace.mode, 'replay')
  assert.equal(trace.modeLabel, '事后回放')
  assert.match(trace.caption, /不是服务端的实时事件流/)

  const byId = Object.fromEntries(trace.steps.map((step) => [step.id, step]))
  assert.equal(byId.retrieve.detail, '带回 2 条法源引用')
  assert.match(byId.generate.detail, /模型 deepseek-chat/)
  assert.match(byId.generate.detail, /提示词 v3/)
  assert.match(byId.generate.detail, /2026-08-07T10:00:00\+00:00/)
  assert.equal(byId.facts.detail, '已确认 1 项 · 待补充 2 项')
  assert.equal(byId.verify.detail, '1 条已核验 · 1 条未核验')
})

test('deriveTraceReplay：响应缺字段时不编造，逐条说明未返回', () => {
  const trace = deriveTraceReplay({ references: [] })
  const byId = Object.fromEntries(trace.steps.map((step) => [step.id, step]))
  assert.equal(byId.retrieve.detail, '本次未带回法源引用')
  assert.equal(byId.generate.detail, '响应未返回模型快照，无法追溯生成信息')
  assert.equal(byId.facts.detail, '已确认 0 项 · 待补充 0 项')
  assert.equal(byId.verify.detail, '无法源引用，无需核验')
})

test('deriveTraceReplay：无核验字段的引用单独计数，不混进已核验', () => {
  const trace = deriveTraceReplay({ references: [SHAPE_B, { title: '裸引用' }] })
  const verify = trace.steps.find((step) => step.id === 'verify')
  assert.equal(verify.detail, '1 条已核验 · 0 条未核验 · 1 条无核验字段')
})

// ── 服务端 trace（后端 trace_json 直读） ──────────────────────────────────────

const SERVER_TRACE = {
  trace_version: 1,
  source: 'legal_consultation',
  steps: [
    { id: 'retrieve', label: '检索法源', status: 'success', duration_ms: 4, detail: '带回 2 条候选法源' },
    { id: 'generate', label: '生成建议', status: 'degraded', duration_ms: 1840, detail: '模型调用未返回结果，已用规则兜底' },
    { id: 'facts', label: '抽取事实', status: 'degraded', duration_ms: 0, detail: '按问题文本抽取：已确认 1 项 · 待补充 2 项' },
    { id: 'verify', label: '核验引用', status: 'skipped', duration_ms: 0, detail: '无法源引用，无需核验' },
  ],
}

test('deriveTraceReplay：服务端有 trace 时优先展示真实步骤，不再事后回放', () => {
  const trace = deriveTraceReplay({ ...RESULT, trace: SERVER_TRACE })
  assert.equal(trace.mode, 'trace')
  assert.equal(trace.modeLabel, '服务端步骤')
  assert.match(trace.caption, /由服务端在处理过程中记录/)
  assert.equal(trace.steps.length, 4)

  const byId = Object.fromEntries(trace.steps.map((step) => [step.id, step]))
  assert.equal(byId.retrieve.detail, '带回 2 条候选法源')
  assert.equal(byId.retrieve.statusLabel, '完成')
  assert.equal(byId.retrieve.duration, '4ms')
  assert.equal(byId.generate.statusLabel, '降级')
  assert.equal(byId.generate.duration, '1.8s')
  assert.equal(byId.verify.statusLabel, '跳过')
  assert.equal(byId.verify.duration, '')
})

test('deriveTraceReplay：trace 形状异常时回退事后回放，不编造步骤', () => {
  for (const bad of [undefined, null, [], {}, 'steps', { steps: [] }, { steps: 'x' }, { steps: [null] }, { steps: [{}] }]) {
    const trace = deriveTraceReplay({ ...RESULT, trace: bad })
    assert.equal(trace.mode, 'replay', `应回退: ${JSON.stringify(bad)}`)
    assert.equal(trace.modeLabel, '事后回放')
    assert.equal(trace.steps.length, 4)
  }
})

test('deriveTraceReplay：服务端步骤缺 id / 文案时兜底，且 id 不重复（Vue key 唯一）', () => {
  const trace = deriveTraceReplay({
    ...RESULT,
    trace: { steps: [{ label: '检索法源' }, { detail: '只给了说明' }, { id: 'retrieve', label: '检索法源' }, { id: 'retrieve', detail: '重复 id' }] },
  })
  assert.equal(trace.mode, 'trace')
  assert.deepEqual(trace.steps.map((step) => step.id), ['step-0', 'step-1', 'retrieve', 'retrieve-3'])
  assert.equal(trace.steps[0].detail, '该步骤未提供说明')
  assert.equal(trace.steps[1].label, '未命名步骤')
})

test('formatStepDuration：毫秒/秒两档，0 与非法值不出文案', () => {
  assert.equal(formatStepDuration(0), '')
  assert.equal(formatStepDuration(-5), '')
  assert.equal(formatStepDuration(null), '')
  assert.equal(formatStepDuration('x'), '')
  assert.equal(formatStepDuration(4), '4ms')
  assert.equal(formatStepDuration(999), '999ms')
  assert.equal(formatStepDuration(1000), '1.0s')
  assert.equal(formatStepDuration(1840), '1.8s')
})

test('deriveCopilotLayers：服务端 trace 与证据卡一起推导，引用仍只算一遍', () => {
  const layers = deriveCopilotLayers({ ...RESULT, trace: SERVER_TRACE })
  assert.equal(layers.trace.mode, 'trace')
  assert.equal(layers.evidence.length, 2)
  assert.equal(layers.attribution.sentences.length, 2)
})

test('deriveAttribution：没有后端逐句映射，降级到整篇答案粒度并写明', () => {
  const attribution = deriveAttribution(RESULT)
  assert.equal(attribution.degraded, true)
  assert.equal(attribution.granularity, 'answer')
  assert.equal(attribution.sentences.length, 2)
  assert.match(attribution.note, /未返回逐句引用映射/)
  assert.match(attribution.note, /全部 2 条证据/)
})

test('deriveAttribution：缺正文或缺证据时说明无法建立对应关系', () => {
  assert.match(deriveAttribution({ references: [SHAPE_A] }).note, /无法建立句与证据的对应关系/)
  assert.match(deriveAttribution({ advice: '一句。' }).note, /无法建立句与证据的对应关系/)
  assert.equal(deriveAttribution({ advice: '一句。' }).sentences.length, 1)
})

test('deriveCopilotLayers：四层一次推导，引用只算一遍', () => {
  const layers = deriveCopilotLayers(RESULT)
  assert.equal(layers.evidence.length, 2)
  assert.equal(layers.trace.steps.length, 4)
  assert.equal(layers.attribution.sentences.length, 2)
  assert.equal(layers.faithfulness.state, 'unchecked')
})

test('deriveCopilotLayers：空响应也能出四层，不抛错（面板降级路径）', () => {
  const layers = deriveCopilotLayers(null)
  assert.deepEqual(layers.evidence, [])
  assert.equal(layers.trace.steps.length, 4)
  assert.deepEqual(layers.attribution.sentences, [])
  assert.equal(layers.faithfulness.state, 'unchecked')
})

// 服务端句级映射（0098 迁移后的 attribution 字段）
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
      text: '试用期不得超过六个月。',
      matches: [
        { reference_index: 1, source_id: 19, title: '劳动合同法 第十九条', score: 0.62, shared_terms: ['试用期', '六个月'] },
        { reference_index: 0, source_id: null, title: '劳动合同法 第十九条', score: 0.35, shared_terms: ['试用期'] },
      ],
    },
    // 第 2 句在法源正文里没有匹配 -> 服务端如实留空
    { index: 1, text: '违法解除需支付赔偿金！', matches: [] },
  ],
}

test('deriveAttribution：服务端句级映射可用时按句给出依据，不再标降级', () => {
  const attribution = deriveAttribution({ ...RESULT, attribution: SERVER_ATTRIBUTION })
  assert.equal(attribution.granularity, 'sentence')
  assert.equal(attribution.degraded, false)
  assert.equal(attribution.sentences.length, 2)
  assert.match(attribution.granularityLabel, /逐句（1\/2 句匹配到依据）/)
  // 第 1 句两条依据按服务端顺序落到证据卡 key 上；第 2 句如实为空
  assert.deepEqual(
    attribution.sentenceMatches[0].map((item) => item.key),
    ['19#1', 'no-source#0'],
  )
  assert.equal(attribution.sentenceMatches[0][0].score, 0.62)
  assert.deepEqual(attribution.sentenceMatches[0][0].sharedTerms, ['试用期', '六个月'])
  assert.deepEqual(attribution.sentenceMatches[1], [])
})

test('deriveAttribution：句级映射里的越界引用下标被丢弃，不指向不存在的证据', () => {
  const attribution = deriveAttribution({
    ...RESULT,
    attribution: {
      ...SERVER_ATTRIBUTION,
      sentences: [
        { index: 0, text: '试用期不得超过六个月。', matches: [{ reference_index: 9, score: 0.9 }] },
        { index: 1, text: '违法解除需支付赔偿金！', matches: [] },
      ],
    },
  })
  assert.equal(attribution.granularity, 'sentence')
  assert.deepEqual(attribution.sentenceMatches[0], [])
  assert.match(attribution.granularityLabel, /0\/2 句匹配到依据/)
})

test('deriveAttribution：分句对不齐或句数不符时回退整篇粒度，并说明原因', () => {
  const mismatchText = deriveAttribution({
    ...RESULT,
    attribution: {
      ...SERVER_ATTRIBUTION,
      sentences: [
        { index: 0, text: '试用期不得超过一年。', matches: [] },
        { index: 1, text: '违法解除需支付赔偿金！', matches: [] },
      ],
    },
  })
  assert.equal(mismatchText.granularity, 'answer')
  assert.equal(mismatchText.degraded, true)
  assert.match(mismatchText.note, /与本地分句对不齐/)

  const mismatchCount = deriveAttribution({
    ...RESULT,
    attribution: { ...SERVER_ATTRIBUTION, sentences: [SERVER_ATTRIBUTION.sentences[0]] },
  })
  assert.equal(mismatchCount.granularity, 'answer')
  assert.match(mismatchCount.note, /与本地分句对不齐/)

  // 形状不对（例如老记录的空对象）走原有的「后端未返回逐句引用映射」口径
  const legacy = deriveAttribution({ ...RESULT, attribution: {} })
  assert.equal(legacy.granularity, 'answer')
  assert.match(legacy.note, /未返回逐句引用映射/)
})

test('deriveVerifiability：在线证据可核验性只在后端给了合法 level 时才展示', () => {
  // 老记录没有 evidence 字段 → null，调用方不渲染（不假装统计过）
  assert.equal(deriveVerifiability({ ...RESULT, evidence: undefined }), null)
  assert.equal(deriveVerifiability({ ...RESULT, evidence: {} }), null)
  assert.equal(deriveVerifiability({ ...RESULT, evidence: { level: 'excellent' } }), null)

  const strong = deriveVerifiability({
    ...RESULT,
    evidence: {
      version: 1,
      level: 'strong',
      label: '引用已逐条核对',
      references: 2,
      article_verified: 2,
      article_missing: 0,
      no_corpus: 0,
      no_numbers: 0,
      unmatched: 0,
      materials: 1,
      note: '2 条引用已与法源条文核对一致；已把 1 份案件材料纳入证据',
    },
  })
  assert.equal(strong.level, 'strong')
  assert.equal(strong.tone, 'success')
  assert.equal(strong.articleVerified, 2)
  assert.equal(strong.materials, 1)
  assert.match(strong.note, /案件材料/)

  // weak 用 warning 色调；缺失计数缺省为 0，不出现 NaN
  const weak = deriveVerifiability({
    ...RESULT,
    evidence: { level: 'weak', label: '引用待核', note: '1 条引用的条号未在已收录条文中找到' },
  })
  assert.equal(weak.tone, 'warning')
  assert.equal(weak.references, 0)
  assert.equal(weak.articleMissing, 0)

  // 四层推导里也要带上它（面板直接读 layers.verifiability）
  const layers = deriveCopilotLayers({ ...RESULT, evidence: { level: 'partial', label: '部分引用已核对' } })
  assert.equal(layers.verifiability.level, 'partial')
  assert.equal(layers.verifiability.tone, 'neutral')
})
