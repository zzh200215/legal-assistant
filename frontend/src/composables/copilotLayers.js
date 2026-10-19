// 咨询答复的「四层可解释性」推导（纯函数，无副作用，可单测）：
//   过程时间线 / 证据卡 / 逐句溯源 / 忠实度。
//
// 输入只有 POST /legal/consultations 的响应（可选字段，两种引用形状都见过）。
// 设计口径：字段缺失一律降级成「未提供/未校验」，不补默认值、不推断结论——
// 面板宁可显示「未校验」并说明原因，也不假装已经核验过。

const NO_VERIFICATION_NOTE = '响应未返回核验结果，无法核验'

const VERIFICATION_LABELS = {
  verified: '已核验',
  unverified: '未核验',
  unknown: '核验状态未知',
}

const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value)
const asText = (value) => (typeof value === 'string' ? value.trim() : '')
const asList = (value) => (Array.isArray(value) ? value : [])

/** 句子切分：按中英文句末标点与换行切，保留标点与原文顺序，丢弃空白片段。 */
export function splitAnswerSentences(value) {
  const raw = typeof value === 'string' ? value : ''
  return (raw.match(/[^。！？；!?;\n]+[。！？；!?;]*/g) || []).map((part) => part.trim()).filter(Boolean)
}

/**
 * 证据卡：每条引用归一成「标题 / 来源 / 版本 / 核验状态」。
 * 引用有两种形状——形状 A 常见，只有 title/citation/version/verification，没有 source_id；
 * 形状 B 才有 source_id/status/effective_date/jurisdiction。缺字段就留空，由展示层决定怎么说明。
 */
export function deriveEvidenceCards(result) {
  return asList(result?.references).map((raw, index) => {
    const reference = isObject(raw) ? raw : {}
    const verification = isObject(reference.verification) ? reference.verification : null
    // verification 缺失（无库环境）时不算「未核验」，更不算「已核验」，单独一态
    const state = !verification ? 'unknown' : verification.verified === true ? 'verified' : 'unverified'
    return {
      index,
      key: `${reference.source_id ?? 'no-source'}#${index}`,
      title: asText(reference.title) || '未命名法源',
      citation: asText(reference.citation),
      version: asText(reference.version),
      sourceId: reference.source_id ?? null,
      linkable: Boolean(reference.source_id),
      sourceStatus: asText(reference.status),
      effectiveDate: asText(reference.effective_date),
      jurisdiction: asText(reference.jurisdiction),
      verification: {
        state,
        label: VERIFICATION_LABELS[state],
        note: verification
          ? asText(verification.verification_note) || (state === 'unverified' ? '未关联法源库条目，无法核验' : '')
          : NO_VERIFICATION_NOTE,
      },
    }
  })
}

/**
 * 忠实度：只有后端明确说「本次跑了校验」（available === true）才给结论；
 * available 缺失、为 false、或声明跑了但没给结论字段，一律「未校验」。
 */
export function deriveFaithfulness(result) {
  const raw = isObject(result?.faithfulness) ? result.faithfulness : null
  if (!raw || raw.available !== true) {
    return {
      state: 'unchecked',
      label: '忠实度未校验',
      detail: '后端本次未运行忠实度校验，不判断答复中的断言是否都被证据支持。',
      unsupported: [],
    }
  }
  const unsupported = asList(raw.unsupported).map(asText).filter(Boolean)
  if (typeof raw.faithful !== 'boolean') {
    return {
      state: 'unchecked',
      label: '忠实度未校验',
      detail: '后端声明本次已运行校验，但响应里没有结论字段，无法判定。',
      unsupported,
    }
  }
  return raw.faithful
    ? {
        state: 'pass',
        label: '忠实度校验通过',
        detail: asText(raw.reason) || '答复中的断言均能在证据中找到支撑。',
        unsupported,
      }
    : {
        state: 'fail',
        label: '忠实度校验未通过',
        detail: asText(raw.reason) || `${unsupported.length} 处断言未被证据支持。`,
        unsupported,
      }
}

const TRACE_STATUS_LABELS = { success: '完成', degraded: '降级', skipped: '跳过', refused: '已拦截' }

/** 步骤状态的中文标签（实时流式帧与最终 trace 共用同一套词表） */
export function traceStatusLabel(status) {
  return TRACE_STATUS_LABELS[status] || ''
}

/** 步骤耗时：1s 以下按毫秒显示，避免出现「0.0s」。 */
export function formatStepDuration(durationMs) {
  const ms = Number(durationMs)
  if (!Number.isFinite(ms) || ms <= 0) return ''
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
}

/**
 * 服务端 trace 归一：只认「非空数组 + 每步至少能读出 label 或 detail」的形状。
 * 任何异常（非数组、元素非对象、两步都空）一律返回 null，让调用方回退到事后回放——
 * 面板宁可不展示，也不展示编造的步骤。
 */
function normalizeServerTrace(raw) {
  const steps = asList(raw?.steps)
  if (!steps.length) return null
  const normalized = []
  const seen = new Set()
  for (const [index, step] of steps.entries()) {
    if (!isObject(step)) return null
    const label = asText(step.label)
    const detail = asText(step.detail)
    if (!label && !detail) return null
    let id = asText(step.id) || `step-${index}`
    if (seen.has(id)) id = `${id}-${index}`
    seen.add(id)
    const status = asText(step.status)
    normalized.push({
      id,
      label: label || '未命名步骤',
      detail: detail || '该步骤未提供说明',
      status,
      statusLabel: TRACE_STATUS_LABELS[status] || '',
      duration: formatStepDuration(step.duration_ms),
    })
  }
  return normalized
}

/**
 * 过程时间线：优先展示服务端记录的真实步骤（含状态与耗时）；
 * 老记录或追问链路没有 trace 时，回退到按响应字段事后回放。
 */
export function deriveTraceReplay(result, evidence = deriveEvidenceCards(result)) {
  const serverSteps = normalizeServerTrace(result?.trace)
  if (serverSteps) {
    return {
      mode: 'trace',
      modeLabel: '服务端步骤',
      caption: '以下步骤由服务端在处理过程中记录（含各阶段耗时与状态）；这里展示的是最终记录，不是实时推送的事件流。',
      steps: serverSteps,
    }
  }

  const snapshot = isObject(result?.model_snapshot) ? result.model_snapshot : {}
  const knownCount = asList(result?.known_facts).filter((fact) => asText(fact)).length
  const missingCount = asList(result?.missing_facts).filter((fact) => asText(fact)).length
  const verified = evidence.filter((card) => card.verification.state === 'verified').length
  const unverified = evidence.filter((card) => card.verification.state === 'unverified').length
  const unknown = evidence.length - verified - unverified
  const promptVersion = snapshot.prompt_version == null ? '' : String(snapshot.prompt_version)

  return {
    mode: 'replay',
    modeLabel: '事后回放',
    caption: '以下步骤由响应字段重建，不是服务端的实时事件流（该接口一次性返回，无过程事件可订阅）。',
    steps: [
      {
        id: 'retrieve',
        label: '检索法源',
        detail: evidence.length ? `带回 ${evidence.length} 条法源引用` : '本次未带回法源引用',
      },
      {
        id: 'generate',
        label: '生成建议',
        detail:
          [
            asText(snapshot.model) && `模型 ${asText(snapshot.model)}`,
            promptVersion && `提示词 v${promptVersion}`,
            asText(snapshot.generated_at) && `快照 ${asText(snapshot.generated_at)}`,
          ]
            .filter(Boolean)
            .join(' · ') || '响应未返回模型快照，无法追溯生成信息',
      },
      {
        id: 'facts',
        label: '抽取事实',
        detail: `已确认 ${knownCount} 项 · 待补充 ${missingCount} 项`,
      },
      {
        id: 'verify',
        label: '核验引用',
        detail: evidence.length
          ? `${verified} 条已核验 · ${unverified} 条未核验${unknown ? ` · ${unknown} 条无核验字段` : ''}`
          : '无法源引用，无需核验',
      },
    ],
  }
}

/**
 * 服务端句级映射归一化：只认「粒度声明为逐句 + 句数与文本和本地分句逐一对齐 + 引用下标在范围内」的结果。
 * 任何一条不满足都返回 null 交给调用方回退——宁可显示整篇粒度，也不能把句子指到错的证据上。
 */
function normalizeServerAttribution(raw, sentences, evidence) {
  if (!isObject(raw) || raw.granularity !== 'sentence') return null
  const items = asList(raw.sentences)
  if (!sentences.length || items.length !== sentences.length) return null
  const aligned = items.every((item, index) => isObject(item) && asText(item.text) === sentences[index])
  if (!aligned) return null
  const sentenceMatches = items.map((item) =>
    asList(item.matches)
      .filter((match) => {
        if (!isObject(match)) return false
        const index = match.reference_index
        return Number.isInteger(index) && index >= 0 && index < evidence.length
      })
      .map((match) => ({
        key: evidence[match.reference_index].key,
        title: evidence[match.reference_index].title,
        score: typeof match.score === 'number' && Number.isFinite(match.score) ? match.score : null,
        sharedTerms: asList(match.shared_terms).map(asText).filter(Boolean),
      })),
  )
  const matchedCount = sentenceMatches.filter((list) => list.length).length
  return {
    sentences,
    sentenceMatches,
    granularity: 'sentence',
    granularityLabel: `粒度：逐句（${matchedCount}/${sentences.length} 句匹配到依据）`,
    degraded: false,
    note: '服务端按句给出引用映射：每句只列出与法源正文词面重合度达标的引用；未列出的句子表示本次没匹配到依据，不代表该句有误。',
  }
}

/**
 * 逐句溯源：优先用服务端的句级映射（`result.attribution`，见 0098 迁移）；
 * 服务端没给或形状/分句对不齐时，退回「某句 → 本题全部证据」，并如实把粒度写进返回值。
 */
export function deriveAttribution(result, evidence = deriveEvidenceCards(result)) {
  const sentences = splitAnswerSentences(result?.advice)
  const server = normalizeServerAttribution(result?.attribution, sentences, evidence)
  if (server) return server
  const declared = isObject(result?.attribution) && result.attribution.granularity === 'sentence'
  return {
    sentences,
    sentenceMatches: sentences.map(() => []),
    granularity: 'answer',
    granularityLabel: '粒度：整篇答案',
    degraded: true,
    note: !sentences.length || !evidence.length
      ? '缺少答复正文或证据，无法建立句与证据的对应关系。'
      : declared
        ? `服务端返回的句级映射与本地分句对不齐，已回退：点击句子定位到的是本题全部 ${evidence.length} 条证据，不是该句的专属依据。`
        : `后端未返回逐句引用映射，点击句子定位到的是本题全部 ${evidence.length} 条证据，不是该句的专属依据。`,
  }
}

/** 一次性推导四层，避免各组件重复算同一份引用。 */
export function deriveCopilotLayers(result) {
  const evidence = deriveEvidenceCards(result)
  return {
    trace: deriveTraceReplay(result, evidence),
    evidence,
    attribution: deriveAttribution(result, evidence),
    faithfulness: deriveFaithfulness(result),
  }
}
