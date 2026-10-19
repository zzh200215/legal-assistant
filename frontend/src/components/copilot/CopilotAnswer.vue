<template>
  <div class="cop-answer-block">
    <!-- 1. 过程时间线：事后回放，口径常显（该接口没有过程事件可订阅） -->
    <CopilotTrace :trace="layers.trace" />

    <!-- 2. 元信息：风险/分类走 presentation 映射；置信度只在服务端真的给了数字时显示 -->
    <div class="cop-meta">
      <span v-if="result.risk_level" class="cop-tag" :class="`cop-tag-${riskTagType(result.risk_level)}`">{{ riskLabel(result.risk_level) }}</span>
      <span v-if="result.category" class="cop-tag cop-tag-accent">{{ categoryLabel(result.category) }}</span>
      <span v-if="confidenceText" class="cop-tag cop-tag-info">置信度 {{ confidenceText }}%</span>
      <!-- 证据可核验性：生成时算好的确定性统计（老记录没有该字段则不显示） -->
      <span
        v-if="verifiability"
        class="cop-tag"
        :class="`cop-tag-${verifiability.tone}`"
        :title="verifiability.note"
      >依据：{{ verifiability.label }}</span>
    </div>

    <!-- 3. 逐句溯源：每句可点，点了高亮支撑证据（粒度见下方说明） -->
    <div v-if="sentences.length" class="cop-answer" role="group" aria-label="答案，按句可点">
      <p v-for="block in blocks" :key="block.key" class="cop-line">
        <button
          v-for="part in block.parts"
          :key="part.index"
          type="button"
          class="cop-sentence"
          :class="{ 'is-active': activeSentence === part.index }"
          :aria-pressed="activeSentence === part.index ? 'true' : 'false'"
          :title="attribution.note"
          @click="selectSentence(part.index)"
        >{{ part.text }}</button>
      </p>
    </div>
    <p v-else-if="adviceText" class="cop-answer">{{ adviceText }}</p>
    <p v-else class="cop-note">本次答复未返回建议正文。</p>

    <p v-if="sentences.length" class="cop-note">{{ attribution.granularityLabel }} · {{ attributionLabel }}</p>
    <!-- 退化说明常显：不说清楚粒度，点句子会被读成「这一句的专属依据」 -->
    <p v-if="sentences.length && attribution.degraded" class="cop-note">{{ attribution.note }}</p>

    <!-- 4. 忠实度标记：后端没跑校验就是「未校验」，不假装已校验 -->
    <div class="cop-faithful" :class="`cop-faithful-${faithfulness.state}`">
      <span class="cop-faithful-label">{{ faithfulness.label }}</span>
      <span class="cop-faithful-detail">{{ faithfulness.detail }}</span>
      <ul v-if="faithfulness.unsupported.length" class="cop-faithful-list">
        <li v-for="(item, index) in faithfulness.unsupported" :key="index">{{ item }}</li>
      </ul>
    </div>

    <!-- 5. 证据卡：标题/来源/版本/核验状态，点开看法源库条文 -->
    <section v-if="cards.length" class="cop-refs">
      <h3 class="cop-section-title">参考依据（{{ cards.length }}）</h3>
      <p v-if="activeSentence !== null" class="cop-note cop-link-note">{{ activeSentenceNote }}</p>
      <CopilotEvidenceCard
        v-for="card in cards"
        :key="card.key"
        :card="card"
        :expanded="expandedKey === card.key"
        :highlighted="highlightedKeys.includes(card.key)"
        :articles="articlesKey === card.key ? sourceDetailArticles : []"
        :loading="loadingKey === card.key"
        @toggle="toggleCard"
      />
    </section>
    <p v-else class="cop-note">本次答复未返回可核对的法源引用。</p>

    <div class="cop-actions">
      <!-- 展开为整页：真实深链到全页咨询结果卡（与通知/历史跳转同一套 query 契约） -->
      <button v-if="fullPageTarget" class="chip" type="button" @click="openFullPage">展开为整页</button>
      <button class="chip" type="button" @click="copyAnswer">{{ copyLabel }}</button>
    </div>

    <p v-if="feedbackNote" class="cop-note">{{ feedbackNote }}</p>
    <AiOutputFeedback
      v-if="result.id"
      :key="feedbackKey"
      target-type="consultation"
      :target-id="result.id"
      :value="result.feedback_score"
      @submit="submitFeedback"
    />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import { errorMessage } from '../../api/errors.js'
import legalWorkspace from '../../api/legalWorkspace'
import { deriveCopilotLayers, splitAnswerSentences } from '../../composables/copilotLayers.js'
import { categoryLabel, riskLabel, riskTagType } from '../../composables/useLegalWorkspacePresentation'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import CopilotEvidenceCard from './CopilotEvidenceCard.vue'
import CopilotTrace from './CopilotTrace.vue'

// 咨询答复渲染块（异步懒加载：面板外壳常驻每个页面，答复 UI 只在真的有答复后才加载）。
// 四层可解释性（过程时间线 / 证据卡 / 逐句溯源 / 忠实度）全部由 copilotLayers.js 从
// props.result 推导，缺字段就降级为「未提供/未校验」——本文件不做任何本地补值或推断。

const props = defineProps({
  result: { type: Object, required: true },
  // 忠实度是生成后的补判动作（判官约 10s），面板在等待期间把标记显示为「校验中」。
  faithfulnessState: { type: String, default: 'idle' },
})
const emit = defineEmits(['feedback-submitted'])

const adviceText = computed(() => props.result.advice || '')
const layers = computed(() => deriveCopilotLayers(props.result))
const cards = computed(() => layers.value.evidence)
const sentences = computed(() => layers.value.attribution.sentences)
// 按原文行分组渲染以保留换行；\n 本身不产生片段，故句索引与 attribution.sentences 逐一对齐
const blocks = computed(() => {
  const out = []
  let index = 0
  adviceText.value.split(/\r?\n/).forEach((line, lineNo) => {
    const parts = splitAnswerSentences(line)
    if (!parts.length) return
    out.push({ key: lineNo, parts: parts.map((text) => ({ text, index: index++ })) })
  })
  return out
})
const attribution = computed(() => layers.value.attribution)
// 证据可核验性：后端生成时的统计（老记录为 null，不渲染）
const verifiability = computed(() => layers.value.verifiability)
// 等待判官时显示「校验中」；判完（或判官不可用）就交回 deriveFaithfulness 的三态口径。
const faithfulness = computed(() => {
  if (props.faithfulnessState === 'checking') {
    return {
      state: 'checking',
      label: '忠实度校验中…',
      detail: '正在逐条核对答复中的断言是否有证据支持。',
      unsupported: [],
    }
  }
  return layers.value.faithfulness
})
const confidenceText = computed(() => {
  const value = props.result.confidence
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
})

const activeSentence = ref(null)
const selectSentence = (index) => {
  activeSentence.value = activeSentence.value === index ? null : index
}
const attributionLabel = computed(() =>
  activeSentence.value === null ? '点任意一句可定位支撑它的证据' : '已按本句高亮证据',
)
// 句级映射可用时只高亮本句真正的依据；退化到整篇粒度时高亮全部证据（口径写在 note 里）
const highlightedKeys = computed(() => {
  if (activeSentence.value === null) return []
  if (attribution.value.granularity !== 'sentence') return cards.value.map((card) => card.key)
  return (attribution.value.sentenceMatches?.[activeSentence.value] || []).map((item) => item.key)
})
const activeSentenceNote = computed(() => {
  const index = activeSentence.value
  if (index === null) return ''
  if (attribution.value.granularity !== 'sentence') {
    return `第 ${index + 1} 句 → 本题全部 ${cards.value.length} 条证据（已高亮）`
  }
  const matches = attribution.value.sentenceMatches?.[index] || []
  if (!matches.length) return `第 ${index + 1} 句 → 本次没有匹配到依据（未高亮任何证据）`
  return `第 ${index + 1} 句 → 依据：${matches.map((item) => item.title).join('、')}（已高亮）`
})

const { openSourceDetail, sourceDetailArticles } = useLegalSourceDetail()
const expandedKey = ref('')
const articlesKey = ref('')
const loadingKey = ref('')
// useLegalSourceDetail 是模块级单例：只把「确实属于当前展开卡」的条文传给卡片，避免串数据
const toggleCard = async (key) => {
  if (expandedKey.value === key) {
    expandedKey.value = ''
    return
  }
  expandedKey.value = key
  articlesKey.value = ''
  loadingKey.value = ''
  const card = cards.value.find((item) => item.key === key)
  if (!card?.linkable) return
  loadingKey.value = key
  await openSourceDetail({ source_id: card.sourceId, title: card.title }, 'copilot')
  if (expandedKey.value !== key) return
  loadingKey.value = ''
  articlesKey.value = key
}

let copyTimer = null
const copyState = ref('')
const copyLabel = computed(() => ({ done: '已复制', failed: '复制失败' })[copyState.value] || '复制答案')
const copyAnswer = async () => {
  if (!adviceText.value) return
  if (copyTimer) clearTimeout(copyTimer)
  try {
    await navigator.clipboard.writeText(adviceText.value)
    copyState.value = 'done'
  } catch {
    // 非安全上下文/无剪贴板权限：如实显示失败，不谎报已复制
    copyState.value = 'failed'
  }
  copyTimer = setTimeout(() => {
    copyState.value = ''
  }, 2000)
}
onBeforeUnmount(() => {
  if (copyTimer) clearTimeout(copyTimer)
})

// 「展开为整页」：深链回案件工作台的咨询 tab，并带上 consultation_id，让结果卡自动恢复
// （LegalWorkspace.vue 的 consultationFocusId → LegalConsultationsTab 的 initialConsultationId，
//  与通知/案件记录的跳转是同一套 query 契约）。没有答复 id 就不给入口，避免点开一个空页。
const router = useRouter()
const fullPageTarget = computed(() => {
  if (!props.result?.id) return null
  const query = { tab: 'consultation', consultation_id: String(props.result.id) }
  if (props.result.case_id) query.case_id = String(props.result.case_id)
  return query
})
const openFullPage = () => {
  if (!fullPageTarget.value) return
  router.push({ path: '/legal-workspace', query: fullPageTarget.value })
}

const feedbackNote = ref('')
// AiOutputFeedback 没有失败回执通道，失败后用新 key 重建以清掉其内部 loading（不改该公共组件）
const feedbackKey = ref(0)
const submitFeedback = async (score, note) => {
  if (!props.result.id) return
  feedbackNote.value = ''
  try {
    await legalWorkspace.submitConsultationFeedback(props.result.id, { score, note })
    emit('feedback-submitted', score)
    feedbackNote.value = '反馈已提交，感谢您的评价。'
  } catch (error) {
    feedbackKey.value += 1
    feedbackNote.value = `反馈提交失败：${errorMessage(error)}`
  }
}
</script>

<style scoped>
.cop-answer-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cop-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.cop-tag {
  display: inline-flex;
  align-items: center;
  padding: 3px var(--space-2);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 600;
}

.cop-tag-danger {
  background: var(--color-danger-light);
  color: var(--color-danger);
}

.cop-tag-warning {
  background: var(--color-warning-light);
  color: var(--color-warning);
}

.cop-tag-success {
  background: var(--color-success-light);
  color: var(--color-success);
}

.cop-tag-neutral {
  background: var(--color-info-light);
  color: var(--color-text-secondary);
}

.cop-answer {
  margin: 0;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg);
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.75;
  word-break: break-word;
}

.cop-answer:not(:has(.cop-sentence)) {
  white-space: pre-wrap;
}

.cop-line {
  margin: 0;
}

.cop-line + .cop-line {
  margin-top: var(--space-2);
}

/* 句子做成 inline 按钮：保持段落观感的同时每句可点，选中句标出定位范围 */
.cop-sentence {
  display: inline;
  padding: 1px 2px;
  border: 0;
  border-radius: var(--radius-xs);
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: inherit;
  text-align: left;
  cursor: pointer;
}

.cop-sentence:hover {
  background: var(--color-primary-light);
}

.cop-sentence.is-active {
  background: var(--color-primary-light);
  color: var(--color-primary-active);
  box-shadow: inset 0 -2px 0 var(--color-primary);
}

.cop-sentence:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 1px;
}

.cop-faithful {
  display: grid;
  gap: 2px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-subtle);
}

.cop-faithful-label {
  font-size: var(--text-xs);
  font-weight: 700;
}

.cop-faithful-unchecked .cop-faithful-label {
  color: var(--color-text-secondary);
}
/* 校验中：与「未校验」区分开，用主色提示这是一次进行中的动作，判完会翻转 */
.cop-faithful-checking .cop-faithful-label {
  color: var(--color-primary);
}

.cop-faithful-pass .cop-faithful-label {
  color: var(--color-success);
}

.cop-faithful-fail .cop-faithful-label {
  color: var(--color-danger);
}

.cop-faithful-detail {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.cop-faithful-list {
  margin: 0;
  padding-left: var(--space-4);
  color: var(--color-danger);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.cop-refs {
  display: grid;
  gap: var(--space-2);
}

.cop-section-title {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 700;
}

.cop-link-note {
  color: var(--color-primary-active);
}

.cop-note {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

.cop-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.chip:hover {
  background: var(--color-bg-alt);
  color: var(--color-text);
}
</style>
