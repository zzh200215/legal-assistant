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
              <el-tag :type="statusTagType(consultResult.status)" size="small" effect="plain">{{ statusLabel(consultResult.status) }}</el-tag>
              <el-tag v-if="consultResult.confidence !== undefined" :type="confidenceTagType(consultResult.confidence)" size="small" effect="plain">置信度 {{ consultResult.confidence }}%</el-tag>
              <el-tag v-if="statusSynced" type="success" size="small" effect="plain" title="已按历史列表重新拉取最新详情">结果已同步</el-tag>
              <!-- 证据可核验性（0100 迁移）：生成时的确定性统计，老记录没有该字段就不显示 -->
              <el-tag
                v-if="consultVerifiability"
                size="small"
                effect="plain"
                :type="consultVerifiability.tone === 'success' ? 'success' : consultVerifiability.tone === 'warning' ? 'warning' : 'info'"
                :title="consultVerifiability.title"
              >依据：{{ consultVerifiability.label }}</el-tag>
              <div class="result-actions">
                <el-button size="small" plain @click="copyResult">复制结果</el-button>
                <el-button size="small" plain @click="exportResultMarkdown">导出 Markdown</el-button>
                <el-button size="small" type="success" plain @click="emit('go-to-review', consultResult)">进入合同审查</el-button>
                <el-button size="small" type="primary" plain @click="emit('go-to-draft', consultResult)">生成文书</el-button>
                <el-button size="small" plain :loading="followUpTaskLoading" @click="createFollowUpTask">生成跟进任务</el-button>
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
              <details class="review-history" @toggle="onReviewHistoryToggle">
                <summary class="export-history-summary">
                  <span>审核记录</span>
                  <span class="app-card-head-meta">按时间倒序</span>
                </summary>
                <p v-if="reviewHistoryLoading" class="legal-muted">正在加载审核记录…</p>
                <p v-else-if="reviewHistoryError" class="legal-muted">{{ reviewHistoryError }}</p>
                <p v-else-if="!reviewHistoryRows.length" class="legal-muted">还没有审核动作。</p>
                <ul v-else class="review-history-list">
                  <li v-for="row in reviewHistoryRows" :key="row.id">
                    <div class="review-history-head">
                      <strong>{{ reviewActionLabel(row.action) }}</strong>
                      <small>{{ formatExportTime(row.created_at) }} · {{ row.reviewer_name || '审核人' }}</small>
                    </div>
                    <p v-if="row.note" class="review-history-note">{{ row.note }}</p>
                    <small v-if="row.from_status || row.to_status" class="review-history-status">
                      {{ statusLabel(row.from_status) }} → {{ statusLabel(row.to_status) }}
                    </small>
                  </li>
                </ul>
              </details>
              <details class="export-history" @toggle="onExportsToggle">
                <summary class="export-history-summary">
                  <span>导出记录</span>
                  <span class="app-card-head-meta">保留 {{ exportRetentionDays }} 天 · 最多 {{ exportMaxRecords }} 条</span>
                </summary>
                <p v-if="exportsLoading" class="legal-muted">正在加载导出记录…</p>
                <p v-else-if="exportsError" class="legal-muted">{{ exportsError }}</p>
                <p v-else-if="!exportRows.length" class="legal-muted">还没有导出记录。</p>
                <ul v-else class="export-history-list">
                  <li v-for="row in exportRows" :key="row.id">
                    <span class="export-history-file">
                      {{ row.filename }}
                      <small>{{ formatExportTime(row.created_at) }} · {{ row.user_name || '未知用户' }}</small>
                    </span>
                    <el-button text size="small" @click="downloadExport(row)">下载</el-button>
                  </li>
                </ul>
                <el-button
                  v-if="exportsHasMore"
                  text
                  size="small"
                  :loading="exportsLoadingMore"
                  @click="loadMoreExports"
                >加载更多</el-button>
              </details>
            </div>
          </section>

          <section v-if="historyVisible" class="app-card history-card">
            <div class="app-card-head">
              <h2>历史咨询</h2>
              <span class="app-card-head-meta">
                <template v-if="historyKeyword.trim()">匹配 {{ searchTotal }} 条</template>
                <template v-else>
                  <template v-if="historyHasMore && consultationHistoryTotal === null">已加载 {{ consultations.length }} 条（还有更早）</template>
                  <template v-else>共 {{ consultationHistoryTotal ?? consultations.length }} 条<template v-if="historyHasMore">（还有更早）</template></template>
                  <template v-if="filteredConsultations.length !== consultations.length"> · 匹配 {{ filteredConsultations.length }} 条</template><template v-if="noReferenceCount"> · 无引用 {{ noReferenceCount }} 条</template>
                </template>
              </span>
            </div>
            <div class="app-card-body history-body">
              <div class="history-chain-filters">
                <input v-model="historyKeyword" type="search" class="history-search" placeholder="搜索问题/答复/法源" aria-label="搜索历史咨询">
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
                <p v-if="searchLoading" class="legal-muted">正在搜索…</p>
                <p v-else-if="searchError" class="legal-muted">{{ searchError }}</p>
                <el-table :data="visibleConsultations" stripe size="small" :empty-text="historyKeyword || historyChainFilter ? '没有符合筛选条件的咨询' : '暂无咨询记录'">
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
                <div v-if="searchHasMore" class="history-load-more">
                  <el-button text size="small" :loading="searchLoading" @click="loadMoreSearch">加载更多搜索结果</el-button>
                </div>
                <div v-if="!historyKeyword.trim() && historyHasMore" class="history-load-more">
                  <el-button text size="small" :loading="historyLoadingMore" @click="loadMoreHistory">加载更多历史咨询</el-button>
                </div>
              </QueryStateView>
            </div>
          </section>
        </div>
</template>

<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
  historyHasMore,
  historyTotal,
  historyCounts,
  historyLoadingMore,
  loadMoreHistory,
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
const historyKeyword = ref('')
const chainLevel = (row) => row.evidence_summary?.level || 'none'
const noReferenceCount = computed(() => consultations.value.filter((row) => chainLevel(row) === 'none').length)
const filteredConsultations = computed(() => {
  let rows = consultations.value
  if (historyChainFilter.value) {
    rows = rows.filter((row) => chainLevel(row) === historyChainFilter.value)
  }
  const keyword = historyKeyword.value.trim().toLocaleLowerCase()
  if (!keyword) return rows
  return rows.filter((row) => {
    const references = Array.isArray(row.references)
      ? row.references.map((ref) => `${ref.title || ''} ${ref.citation || ''}`).join(' ')
      : ''
    return [row.question, row.advice, references]
      .some((value) => String(value || '').toLocaleLowerCase().includes(keyword))
  })
})

const consultationHistoryTotal = computed(() => (
  historyTotal.value ?? historyCounts.value?.consultations ?? null
))
const searchResults = ref(null)
const searchLoading = ref(false)
const searchError = ref(null)
const searchTotal = ref(0)
const searchHasMore = ref(false)
const searchCursor = ref(null)
let searchTimer = null
const visibleConsultations = computed(() => {
  if (!historyKeyword.value.trim() || !searchResults.value) return filteredConsultations.value
  return historyChainFilter.value
    ? searchResults.value.filter((row) => chainLevel(row) === historyChainFilter.value)
    : searchResults.value
})
const runSearch = async (cursor = null) => {
  const keyword = historyKeyword.value.trim()
  if (!keyword) return
  searchLoading.value = true
  searchError.value = null
  try {
    const { data } = await legalWorkspace.searchLegalConsultations({
      q: keyword,
      limit: 20,
      ...(props.caseId ? { case_id: props.caseId } : {}),
      ...(cursor ? { cursor } : {}),
    })
    if (historyKeyword.value.trim() !== keyword) return
    const items = data?.items || []
    searchResults.value = cursor ? [...(searchResults.value || []), ...items] : items
    searchTotal.value = data?.total || 0
    searchHasMore.value = Boolean(data?.has_more)
    searchCursor.value = data?.next_cursor || null
  } catch (error) {
    searchError.value = error.response?.data?.detail || '搜索失败'
  } finally {
    searchLoading.value = false
  }
}
const loadMoreSearch = () => runSearch(searchCursor.value)
watch(historyKeyword, (value) => {
  clearTimeout(searchTimer)
  const keyword = value.trim()
  if (!keyword) {
    searchResults.value = null
    searchTotal.value = 0
    searchHasMore.value = false
    searchCursor.value = null
    searchError.value = null
    return
  }
  searchCursor.value = null
  searchTimer = setTimeout(() => runSearch(), 300)
})
watch(() => props.caseId, () => {
  searchResults.value = null
  searchTotal.value = 0
  searchHasMore.value = false
  searchCursor.value = null
  searchError.value = null
  if (historyKeyword.value.trim()) runSearch()
})
onBeforeUnmount(() => clearTimeout(searchTimer))
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

const resultToText = () => {
  const result = consultResult.value
  if (!result) return ''
  const facts = (result.known_facts || []).join('；') || '暂无'
  const references = asReferenceList(result.references)
    .map((ref) => `${ref.title || '法源'} ${ref.citation || ''}`.trim())
    .filter(Boolean)
    .join('\n') || '暂无'
  return [
    `咨询问题：${result.question || ''}`,
    `当前状态：${statusLabel(result.status)}`,
    `结论与建议：${result.advice || ''}`,
    `已知事实：${facts}`,
    `参考依据：\n${references}`,
  ].join('\n')
}
const fallbackCopy = (text) => {
  const area = document.createElement('textarea')
  area.value = text
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let copied = false
  try {
    copied = document.execCommand('copy')
  } finally {
    document.body.removeChild(area)
  }
  return copied
}
const followUpTaskLoading = ref(false)
const copyResult = async () => {
  const text = resultToText()
  if (!text) return
  let copied = false
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      copied = true
    } catch {
      copied = false
    }
  }
  if (!copied) copied = fallbackCopy(text)
  if (copied) ElMessage.success('咨询结果已复制')
  else ElMessage.error('复制失败，请手动选择结果文本')
}
const exportResultMarkdown = async () => {
  const id = consultResult.value?.id
  if (!id) return
  try {
    const { data } = await legalWorkspace.exportLegalConsultationMarkdown(id)
    const url = URL.createObjectURL(data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `法律咨询-${id}.md`
    anchor.click()
    URL.revokeObjectURL(url)
    ElMessage.success('Markdown 已导出')
    if (exportsOpen.value) await loadExports()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '导出失败')
  }
}

const createFollowUpTask = async () => {
  const id = consultResult.value?.id
  if (!id || followUpTaskLoading.value) return
  followUpTaskLoading.value = true
  try {
    const { data } = await legalWorkspace.createConsultationFollowUpTask(id)
    ElMessage.success(data?.created === false ? `已存在跟进任务 #${data.id}` : `已生成跟进任务 #${data.id}`)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '生成跟进任务失败')
  } finally {
    followUpTaskLoading.value = false
  }
}

const exportsOpen = ref(false)
const exportRows = ref([])
const exportsLoading = ref(false)
const exportsLoadingMore = ref(false)
const exportsHasMore = ref(false)
const exportNextCursor = ref(null)
const exportRetentionDays = ref(90)
const exportMaxRecords = ref(20)
const exportsError = ref(null)
const formatExportTime = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '')
const loadExports = async (reset = true) => {
  const id = consultResult.value?.id
  if (!id) return
  const loading = reset ? exportsLoading : exportsLoadingMore
  if (!reset && !exportsHasMore.value) return
  loading.value = true
  exportsError.value = null
  try {
    const params = { limit: 20 }
    if (!reset && exportNextCursor.value) params.cursor = exportNextCursor.value
    const { data } = await legalWorkspace.listLegalConsultationExports(id, { params })
    const items = data?.items || []
    exportRows.value = reset ? items : [...exportRows.value, ...items]
    exportsHasMore.value = Boolean(data?.has_more)
    exportNextCursor.value = data?.next_cursor || null
    if (data?.retention_days) exportRetentionDays.value = data.retention_days
    if (data?.max_records) exportMaxRecords.value = data.max_records
  } catch (error) {
    exportsError.value = error.response?.data?.detail || '加载导出记录失败'
  } finally {
    loading.value = false
  }
}
const loadMoreExports = () => loadExports(false)
const onExportsToggle = (event) => {
  exportsOpen.value = event.target.open
  if (exportsOpen.value) loadExports()
}
const downloadExport = async (row) => {
  try {
    const { data } = await legalWorkspace.downloadLegalConsultationExport(row.id)
    const url = URL.createObjectURL(data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = row.filename || `法律咨询-${consultResult.value?.id || ''}.md`
    anchor.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已下载导出快照')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '下载失败')
  }
}
watch(() => consultResult.value?.id, () => {
  exportRows.value = []
  exportsError.value = null
  if (exportsOpen.value) loadExports()
})

const reviewHistoryOpen = ref(false)
const reviewHistoryRows = ref([])
const reviewHistoryLoading = ref(false)
const reviewHistoryError = ref(null)
const REVIEW_ACTION_LABELS = {
  approve: '审核通过', return: '退回补充', offline: '转线下',
  submit_review: '提交审核', comment: '批注',
}
const reviewActionLabel = (action) => REVIEW_ACTION_LABELS[action] || action || '审核动作'
const loadReviewHistory = async () => {
  const id = consultResult.value?.id
  if (!id) return
  reviewHistoryLoading.value = true
  reviewHistoryError.value = null
  try {
    const { data } = await legalWorkspace.getReviewHistory('consultation', id)
    reviewHistoryRows.value = data?.history || []
  } catch (error) {
    reviewHistoryError.value = error.response?.data?.detail || '加载审核记录失败'
  } finally {
    reviewHistoryLoading.value = false
  }
}
const onReviewHistoryToggle = (event) => {
  reviewHistoryOpen.value = event.target.open
  if (reviewHistoryOpen.value) loadReviewHistory()
}
watch(() => consultResult.value?.id, () => {
  reviewHistoryRows.value = []
  reviewHistoryError.value = null
  if (reviewHistoryOpen.value) loadReviewHistory()
})

// 历史咨询 → 结果卡：点击行内"查看结果"加载完整详情（含送审按钮），替代刷新后丢失的会话内状态
const focusRowId = ref(null)
// 结果卡与历史列表一致性：页面级 ref 可能停在提交时的版本。
// 比较 status 与 updated_at；列表更新（审核通过、正文/引用变化）时按 id 重拉详情。
const statusSyncing = ref(false)
const statusSynced = ref(false)
const syncResultStatus = async () => {
  const result = consultResult.value
  if (!result?.id || !consultations.value.length || statusSyncing.value) return
  const row = consultations.value.find((item) => Number(item.id) === Number(result.id))
  if (!row) return
  const statusChanged = Boolean(row.status && row.status !== result.status)
  // 优先比内容指纹：正文/引用/核验/审核字段任一变化都会变；
  // 老记录没有 content_version 时退回 updated_at（时间戳精度/时钟不可靠）。
  const versionChanged = Boolean(
    row.content_version && result.content_version && row.content_version !== result.content_version
  )
  const missingVersion = !row.content_version || !result.content_version
  const resultStamp = Date.parse(result.updated_at || '')
  const rowStamp = Date.parse(row.updated_at || '')
  const newer = missingVersion && Number.isFinite(rowStamp)
    && (!Number.isFinite(resultStamp) || rowStamp > resultStamp)
  if (!statusChanged && !versionChanged && !newer) return
  statusSyncing.value = true
  try {
    await openConsultationDetail(result.id)
    statusSynced.value = true
  } finally {
    statusSyncing.value = false
  }
}
watch([() => consultResult.value?.id, consultations], () => { void syncResultStatus() }, { flush: 'post' })
watch(() => consultResult.value?.id, () => { statusSynced.value = false })
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
.history-search { height: 28px; min-width: 180px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-elevated); color: var(--color-text-primary); font-size: var(--text-xs); }
.history-search:focus { border-color: var(--color-primary); outline: none; }
.history-load-more { display: flex; justify-content: center; margin-top: var(--space-2); }
.history-chain-filters select { height: 28px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-elevated); color: var(--color-text-primary); font-size: var(--text-xs); }
.chain-chip { height: 28px; padding: 0 10px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-subtle); color: var(--color-text-secondary); font-size: var(--text-xs); cursor: pointer; }
.chain-chip:hover:not(:disabled) { color: var(--color-text-primary); }
.chain-chip.active { border-color: var(--color-primary); color: var(--color-primary); background: var(--color-primary-light); }
.chain-chip:disabled { opacity: 0.5; cursor: not-allowed; }

.comparison-summary { margin: 0 0 var(--space-2); color: var(--color-text-primary); font-size: var(--text-sm); }
.comparison-refs { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-1); }
.comparison-label { color: var(--color-text-secondary); font-size: var(--text-xs); }
.comparison-meta { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); }

.export-history { margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
.review-history { margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
.review-history-list { display: grid; gap: var(--space-3); margin: var(--space-2) 0 0; padding: 0; list-style: none; }
.review-history-list li { display: grid; gap: 2px; }
.review-history-head { display: flex; align-items: center; justify-content: space-between; gap: var(--space-2); }
.review-history-head strong { font-size: var(--text-sm); }
.review-history-head small, .review-history-status { color: var(--color-text-muted); font-size: var(--text-xs); }
.review-history-note { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); }
.export-history-summary { display: flex; align-items: center; justify-content: space-between; cursor: pointer; color: var(--color-text-secondary); font-size: var(--text-sm); list-style: none; }
.export-history-summary::-webkit-details-marker { display: none; }
.export-history-list { display: grid; gap: var(--space-2); margin: var(--space-2) 0 0; padding: 0; list-style: none; }
.export-history-list li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); }
.export-history-file { display: grid; gap: 2px; min-width: 0; color: var(--color-text-primary); font-size: var(--text-xs); }
.export-history-file small { color: var(--color-text-muted); font-size: var(--text-xs); }
</style>
