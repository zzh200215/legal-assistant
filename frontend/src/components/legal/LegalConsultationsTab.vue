<template>
        <div class="tab-panel">
          <section class="app-card">
            <div class="app-card-head">
              <h2>法律咨询</h2>
              <span class="app-card-head-meta">基于案件资料与法源库作答</span>
            </div>
            <div class="app-card-body">
              <el-form ref="consultFormRef" :model="consultForm" :rules="consultRules" @submit.prevent="submitConsultation">
                <el-form-item prop="question">
                  <el-input v-model="consultForm.question" type="textarea" :rows="4" placeholder="例如：我在公司工作了3年，公司突然辞退我，没有支付经济补偿金..." maxlength="12000" show-word-limit />
                </el-form-item>
                <div class="consult-actions">
                  <el-button type="primary" :loading="consultLoading" @click="submitConsultation">提交咨询</el-button>
                  <span v-if="consultLoading" class="staged-hint" aria-live="polite">{{ consultStageText }}</span>
                </div>
              </el-form>
            </div>
          </section>

          <section v-if="consultResult" class="app-card result-card">
            <div class="app-card-head result-header">
              <h2>咨询结果</h2>
              <el-tag v-if="quotaHint('consultation')" size="small" effect="plain" :type="quotaSummary?.consultation?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('consultation') }}</el-tag>
              <el-tag :type="riskTagType(consultResult.risk_level)" size="small">{{ riskLabel(consultResult.risk_level) }}</el-tag>
              <el-tag v-if="consultResult.confidence !== undefined" :type="confidenceTagType(consultResult.confidence)" size="small" effect="plain">置信度 {{ consultResult.confidence }}%</el-tag>
              <!-- 证据可核验性（0100 迁移）：生成时的确定性统计，老记录没有该字段就不显示 -->
              <el-tag
                v-if="consultVerifiability"
                size="small"
                effect="plain"
                :type="consultVerifiability.tone === 'success' ? 'success' : consultVerifiability.tone === 'warning' ? 'warning' : 'info'"
                :title="consultVerifiability.title"
              >依据：{{ consultVerifiability.label }}</el-tag>
              <div class="result-actions">
                <el-button size="small" type="success" plain @click="emit('go-to-review', consultResult)">进入合同审查</el-button>
                <el-button size="small" type="primary" plain @click="emit('go-to-draft', consultResult)">生成文书</el-button>
                <el-button v-if="consultResult.status === 'pending_review' || consultResult.status === 'needs_lawyer_review'" size="small" type="primary" @click="submitConsultForReview">提交律师审核</el-button>
              </div>
            </div>
            <div class="app-card-body">
              <p v-if="consultResult.question" class="app-question-bubble">{{ consultResult.question }}</p>
              <div class="app-answer-block result-body">
                <section class="app-answer-section">
                  <p class="app-answer-kicker">结论与建议</p>
                  <p class="app-answer-text">{{ consultResult.advice }}</p>
                </section>

                <section class="app-answer-section">
                  <p class="app-answer-kicker">已知事实</p>
                  <ul v-if="consultResult.known_facts?.length" class="app-answer-list">
                    <li v-for="f in consultResult.known_facts" :key="f">{{ f }}</li>
                  </ul>
                  <p v-else class="legal-muted">暂无</p>
                </section>

                <section class="app-answer-section">
                  <p class="app-answer-kicker">待补充事实</p>
                  <ul v-if="consultResult.missing_facts?.length" class="app-answer-list is-missing">
                    <li v-for="f in consultResult.missing_facts" :key="f">{{ f }}</li>
                  </ul>
                  <span v-else class="app-tag is-ok">无缺失</span>
                </section>

                <section v-if="consultResult.comparison" class="app-answer-section comparison-block">
                  <p class="app-answer-kicker">补充前后对比</p>
                  <p class="comparison-summary">{{ consultResult.comparison.summary }}</p>
                  <div v-if="consultResult.comparison.references.added?.length" class="comparison-refs">
                    <span class="comparison-label">新增引用</span>
                    <span v-for="ref in consultResult.comparison.references.added" :key="`add-${ref.source_id || ref.title}`" class="app-tag is-ok">{{ ref.title || `法源 ${ref.source_id}` }}</span>
                  </div>
                  <div v-if="consultResult.comparison.references.removed?.length" class="comparison-refs">
                    <span class="comparison-label">移除引用</span>
                    <span v-for="ref in consultResult.comparison.references.removed" :key="`del-${ref.source_id || ref.title}`" class="app-tag">{{ ref.title || `法源 ${ref.source_id}` }}</span>
                  </div>
                  <p class="comparison-meta">
                    忠实度：{{ faithfulnessLabel(consultResult.comparison.faithfulness.before) }} → {{ faithfulnessLabel(consultResult.comparison.faithfulness.after) }}
                  </p>
                </section>

                <section class="app-answer-section">
                  <p class="app-answer-kicker">参考依据</p>
                  <p v-if="consultResult.evidence_summary?.references" class="chain-line">{{ chainDetail(consultResult.evidence_summary) }}</p>
                  <div v-if="referenceItems.length" class="app-evidence-list">
                    <div v-for="r in referenceItems" :key="r.source_id || r.title" class="app-evidence ref-item">
                      <div class="app-evidence-head">
                        <button class="app-evidence-title ref-title" type="button" @click="openSourceDetail(r)">{{ r.title }}</button>
                        <span v-if="r.verification" class="app-verify-badge" :class="r.verification.verified ? 'is-ok' : 'is-warn'">{{ r.verification.verified ? '已核验' : '未核验' }}</span>
                      </div>
                      <p class="app-evidence-text">{{ r.citation }}</p>
                      <div class="app-evidence-foot">
                        <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                        <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                        <span v-if="r.verification?.verification_note" class="app-evidence-meta">{{ r.verification.verification_note }}</span>
                      </div>
                    </div>
                  </div>
                  <p v-else class="legal-muted">暂无</p>
                </section>
              </div>

              <div class="followup-section">
                <el-input ref="followupInputRef" v-model="followupQuestion" placeholder="针对此咨询追问..." :disabled="followupLoading">
                  <template #append>
                    <el-button :loading="followupLoading" @click="submitFollowup">追问</el-button>
                  </template>
                </el-input>
              </div>
              <AiOutputFeedback :target-type="'consultation'" :target-id="consultResult.id" :value="consultResult.feedback_score" @submit="submitConsultFeedback" />
            </div>
          </section>

          <section v-if="historyVisible" class="app-card history-card">
            <div class="app-card-head">
              <h2>历史咨询</h2>
              <span class="app-card-head-meta">共 {{ consultations.length }} 条<template v-if="noReferenceCount"> · 无引用 {{ noReferenceCount }} 条</template></span>
            </div>
            <div class="app-card-body history-body">
              <div class="history-chain-filters">
                <select v-model="historyChainFilter" aria-label="按依据筛选">
                  <option value="">全部依据</option>
                  <option value="none">无引用</option>
                  <option value="weak">弱</option>
                  <option value="partial">部分</option>
                  <option value="strong">强</option>
                </select>
                <button
                  type="button" class="chain-chip" :class="{ active: historyChainFilter === 'none' }"
                  :disabled="!noReferenceCount && historyChainFilter !== 'none'"
                  @click="historyChainFilter = historyChainFilter === 'none' ? '' : 'none'"
                >无引用 {{ noReferenceCount }}</button>
              </div>
              <!-- 三态区分（ux-audit P1-3/M-13）：加载失败与"暂无咨询"分开呈现 -->
              <QueryStateView :status="historyStatus" :error="historyError" min-height="0px" @retry="loadConsultations">
                <el-table :data="filteredConsultations" stripe size="small" :empty-text="historyChainFilter ? '没有符合筛选条件的咨询' : '暂无咨询记录'">
                  <el-table-column prop="id" label="ID" width="60" />
                  <el-table-column prop="category" label="分类" width="120">
                    <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
                  </el-table-column>
                  <el-table-column prop="question" label="问题" show-overflow-tooltip />
                  <el-table-column v-if="caseId" prop="submitter_name" label="提交人" width="100">
                    <template #default="{ row }">{{ row.submitter_name || '—' }}</template>
                  </el-table-column>
                  <el-table-column prop="risk_level" label="风险" width="80">
                    <template #default="{ row }">
                      <el-tag :type="riskTagType(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="依据" width="150">
                    <template #default="{ row }">
                      <el-tag size="small" effect="plain" :type="chainTagType(row.evidence_summary)">{{ chainText(row.evidence_summary) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="status" label="状态" width="120">
                    <template #default="{ row }">
                      <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="100">
                    <template #default="{ row }">
                      <el-button size="small" link type="primary" :loading="openDetailLoading && focusRowId === row.id" @click="openHistoryResult(row)">查看结果</el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </QueryStateView>
            </div>
          </section>
        </div>
</template>

<script setup>
import { computed, inject, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
// 结果卡改为方向 B 的 .app-card 版式后不再用 el-card / el-descriptions：
// 卡片面、标题条与分区标题都由 design-tokens 的共享类提供，少两个 Element Plus chunk。
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import QueryStateView from '../QueryStateView.vue'
import { useLegalConsultations } from '../../composables/useLegalConsultations'
import { CONSULT_RESULT_KEY } from '../../composables/consultationResultKey'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import { useStagedProgress } from '../../composables/useStagedProgress'
import { deriveVerifiability } from '../../composables/copilotLayers'
import { asReferenceList } from '../../utils/references'
import {
  riskTagType, riskLabel, categoryLabel,
  sourceStatusType, sourceStatusLabel, statusTagType, statusLabel,
} from '../../composables/useLegalWorkspacePresentation'

// 咨询侧证据链（服务端 evidence_summary）：强/部分/弱/无引用 → 标签语义
const CHAIN_TAG_TYPES = { strong: 'success', partial: 'warning', weak: 'danger', none: 'info' }
const chainTagType = (summary) => CHAIN_TAG_TYPES[summary?.level] || 'info'
const chainText = (summary) => {
  if (!summary || summary.level === 'none') return '无引用'
  const refs = `${summary.references} 条引用`
  return summary.sentences ? `${refs} · ${summary.attributed}/${summary.sentences} 句` : refs
}
const FAITHFULNESS_LABELS = { pass: '通过', fail: '未通过', unchecked: '未校验' }
const faithfulnessLabel = (state) => FAITHFULNESS_LABELS[state] || state || '未校验'
const chainDetail = (summary) => {
  const parts = []
  if (summary.sentences) parts.push(`句级溯源 ${summary.attributed}/${summary.sentences} 句`)
  else parts.push('未生成句级溯源')
  if (summary.faithfulness === 'pass') parts.push('忠实度通过')
  else if (summary.faithfulness === 'fail') parts.push(`忠实度未通过（${summary.unsupported} 处）`)
  else parts.push('忠实度未校验')
  if (!summary.active_references) parts.push('引用均已失效')
  return parts.join(' · ')
}
const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const props = defineProps({
  caseId: { type: Number, default: null },
  // 通知/案件记录直达时聚焦的咨询 ID：挂载及变化时自动恢复结果卡（D12 回归发现修复）
  initialConsultationId: { type: Number, default: null },
  onReviewSubmitted: { type: Function, default: null },
})
const emit = defineEmits(['go-to-draft', 'go-to-review'])

const { quotaSummary, quotaHint, loadQuota } = useQuota()
// 只取 openSourceDetail：核验状态现在由结果卡自己的 .app-verify-badge 表达（已核验 / 未核验）
const { openSourceDetail } = useLegalSourceDetail()
// 页面级共享的结果 ref（由 LegalWorkspace provide）；不在工作区时退回本地 state。
const sharedConsultResult = inject(CONSULT_RESULT_KEY, null)

const {
  consultForm,
  consultLoading,
  consultResult,
  consultations,
  consultationsQuery,
  followupQuestion,
  followupLoading,
  openDetailLoading,
  openConsultationDetail,
  loadConsultations,
  submitConsultation: runConsultation,
  submitFollowup,
  submitConsultForReview,
} = useLegalConsultations({
  client: legalWorkspace,
  message: ElMessage,
  confirm: ElMessageBox.confirm,
  caseId: computed(() => props.caseId),
  onReviewSubmitted: props.onReviewSubmitted,
  result: sharedConsultResult,
})

// 历史咨询三态（ux-audit P1-3/M-13）：失败态可重试，不再与"暂无咨询"混同
// 依据筛选：无引用可单独列出（level 缺失按 none 处理，老记录即「没引用」）
const historyChainFilter = ref('')
const chainLevel = (row) => row.evidence_summary?.level || 'none'
const noReferenceCount = computed(() => consultations.value.filter((row) => chainLevel(row) === 'none').length)
const filteredConsultations = computed(() => (
  historyChainFilter.value
    ? consultations.value.filter((row) => chainLevel(row) === historyChainFilter.value)
    : consultations.value
))
const historyStatus = computed(() => {
  if (consultationsQuery.isLoading.value) return 'loading'
  if (consultationsQuery.isError.value) return 'error'
  if (!consultations.value.length) return 'empty'
  return 'success'
})
const historyError = computed(() => (consultationsQuery.isError.value ? consultationsQuery.error.value : null))
const historyVisible = computed(() => historyStatus.value !== 'empty')

// references 由模型产出，历史上出现过 null / 缺标题的条目：渲染前先过滤，
// 否则一个坏条目会让整个结果卡渲染报错（白屏）。
const referenceItems = computed(() => asReferenceList(consultResult.value?.references))

// 证据可核验性：后端生成时算好的统计（老记录没有 evidence 字段 → null，不显示标签）
const consultVerifiability = computed(() => deriveVerifiability(consultResult.value))

// 历史咨询 → 结果卡：点击行内"查看结果"加载完整详情（含送审按钮），替代刷新后丢失的会话内状态
const focusRowId = ref(null)
const openHistoryResult = async (row) => {
  focusRowId.value = row.id
  await openConsultationDetail(row.id)
  if (consultResult.value?.id === row.id) focusRowId.value = null
}

watch(() => props.initialConsultationId, (id) => {
  // 结果已由页面级 ref 保留时不再重复请求（例如从文书/合同审查切回咨询）；
  // 刷新/深链时页面级 ref 为空，才按 URL 的 consultation_id 恢复。
  if (id && consultResult.value?.id !== id) openConsultationDetail(id)
}, { immediate: true })

// 结果卡与 URL 焦点保持一致：提交/追问/打开历史后把 consultation_id 写进 URL，
// 刷新或分享链接时才能恢复同一张结果卡。
const route = useRoute()
const router = useRouter()
watch(() => consultResult.value?.id, (id) => {
  if (!id) return
  if (String(route.query.consultation_id || '') === String(id)) return
  router.replace({ query: { ...route.query, consultation_id: String(id) } })
})

// 通知「去补充」直达：带 supplement=1 时把追问框预填好并聚焦
const followupInputRef = ref(null)
const supplementApplied = ref(false)
const applySupplementPrefill = async () => {
  if (route.query.supplement !== '1' || supplementApplied.value || !consultResult.value) return
  followupQuestion.value = '请补充相关法源依据后重新回答上述问题。'
  supplementApplied.value = true
  await nextTick()
  followupInputRef.value?.focus?.()
}
watch([() => consultResult.value?.id, () => route.query.supplement], () => {
  supplementApplied.value = false
  void applySupplementPrefill()
}, { immediate: true })

// 内联校验（ux-audit M-11）：问题必填即时提示，替代提交后的全局 toast
const consultFormRef = ref(null)
const consultRules = {
  question: [{ required: true, message: '请输入法律问题', trigger: 'blur', transform: (v) => (v || '').trim() }],
}

const submitConsultation = async () => {
  const valid = await consultFormRef.value?.validate().catch(() => false)
  if (!valid) return
  runConsultation()
  loadQuota()
}

// LLM 生成约需 20-30 秒，阶段性提示缓解等待焦虑（ux-audit M-6）
const consultStageText = useStagedProgress(consultLoading, [
  '正在检索相关法源…',
  '正在分析问题要点与风险…',
  '正在生成法律建议…',
])

const submitConsultFeedback = async (score, note) => {
  if (!consultResult.value?.id) return
  try {
    await legalWorkspace.submitConsultationFeedback(consultResult.value.id, { score, note })
    consultResult.value.feedback_score = score
    ElMessage.success('反馈已提交，感谢您的评价')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
  }
}

onMounted(() => {
  loadConsultations()
})
</script>

<style scoped>
.tab-panel {
  display: grid;
  gap: var(--space-5);
}

.consult-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.staged-hint {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

/* 结果卡：标题条左侧是标题 + 风险/置信度标签，操作按钮推到最右 */
.result-header {
  flex-wrap: wrap;
  row-gap: 6px;
}

.result-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.result-body {
  margin-top: var(--space-4);
}

.app-answer-list.is-missing {
  color: var(--color-warning);
}

/* 证据行：标题（可点开法源条文）+ 核验徽标 + 条文 + 状态/版本/口径 */
.app-evidence-title {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-primary);
  font: inherit;
  font-size: var(--text-sm);
  font-weight: 600;
  text-align: left;
  cursor: pointer;
}

.app-evidence-title:hover {
  color: var(--color-primary-hover);
  text-decoration: underline;
}

.app-evidence-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.app-evidence-foot .ref-version {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.followup-section {
  margin-top: var(--space-4);
}

/* 历史咨询表格自带内边距，卡片体里收紧一点，避免双层留白 */
.history-body {
  padding-top: var(--space-3);
}

.chain-line { margin: 0 0 var(--space-2); color: var(--color-text-secondary); font-size: var(--text-xs); }

.history-chain-filters { display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-2); }
.history-chain-filters select { height: 28px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-elevated); color: var(--color-text-primary); font-size: var(--text-xs); }
.chain-chip { height: 28px; padding: 0 10px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-subtle); color: var(--color-text-secondary); font-size: var(--text-xs); cursor: pointer; }
.chain-chip:hover:not(:disabled) { color: var(--color-text-primary); }
.chain-chip.active { border-color: var(--color-primary); color: var(--color-primary); background: var(--color-primary-light); }
.chain-chip:disabled { opacity: 0.5; cursor: not-allowed; }

.comparison-summary { margin: 0 0 var(--space-2); color: var(--color-text-primary); font-size: var(--text-sm); }
.comparison-refs { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-1); }
.comparison-label { color: var(--color-text-secondary); font-size: var(--text-xs); }
.comparison-meta { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); }
</style>
