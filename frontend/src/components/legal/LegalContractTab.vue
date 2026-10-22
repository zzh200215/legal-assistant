<template>
  <div class="contract-workspace">
    <template v-if="!contractResult">
      <!-- 录入态（方向 B）：页头 + 单卡片表单，上传 / 粘贴入口与行为不变 -->
      <header class="app-page-head">
        <div class="app-page-head-main">
          <h2 class="app-page-title">开始合同审查</h2>
          <p class="app-page-sub">导入合同或粘贴原文，审查结果会归档到当前案件。</p>
        </div>
      </header>

      <section class="app-card contract-intake">
        <div class="app-card-head">
          <h2>合同信息</h2>
          <span class="app-card-head-meta">上传文件或粘贴原文均可</span>
        </div>
        <div class="app-card-body intake-body">
          <label class="field-label" for="contract-title">合同名称</label>
          <el-input id="contract-title" v-model="contractForm.title" placeholder="例如：技术服务合同" />
          <label class="field-label" for="contract-content">合同原文</label>
          <el-input id="contract-content" v-model="contractForm.content" type="textarea" :rows="13" placeholder="粘贴合同全文或主要条款..." maxlength="50000" show-word-limit />
          <div class="intake-actions">
            <el-upload :show-file-list="false" :before-upload="handleContractUpload" accept=".pdf,.docx,.doc,.txt,.md">
              <el-button :loading="uploadLoading">上传合同文件</el-button>
            </el-upload>
            <el-button type="primary" :loading="contractLoading" @click="submitContractReview">开始审查</el-button>
            <div v-if="contractLoading" class="staged-hint" aria-live="polite">{{ contractStageText }}</div>
          </div>
        </div>
      </section>
    </template>

    <template v-else>
      <!-- 结果态（方向 B）：页头承载返回、标题、状态标签与动作 -->
      <header class="app-page-head">
        <div class="document-heading">
          <button class="back-action" type="button" aria-label="新建一次审查" title="新建一次审查" @click="startNewReview">‹</button>
          <div class="app-page-head-main document-title-group">
            <h2 class="app-page-title document-title">{{ contractResult.title || contractForm.title || '未命名合同' }}</h2>
            <div class="document-meta">
              <span class="app-tag" :class="statusTagClass(contractResult.status)">{{ statusLabel(contractResult.status) }}</span>
              <span class="app-tag">Version {{ contractResult.version || 1 }}</span>
              <span v-if="contractResult.updated_at" class="document-time">{{ formatDate(contractResult.updated_at) }}</span>
            </div>
          </div>
        </div>
        <div class="app-page-head-actions">
          <el-button size="small" @click="exportReview">导出审查意见</el-button>
          <el-button size="small" type="primary" :loading="reviewSubmitting" :disabled="!canSubmitForReview" @click="submitForLawyerReview">提交律师审核</el-button>
        </div>
      </header>

      <section v-if="contractResult.summary" class="app-card review-summary">
        <div class="app-card-body">
          <h3 class="app-card-section-title">审查摘要</h3>
          <p class="review-summary-text">{{ contractResult.summary }}</p>
        </div>
      </section>

      <div class="contract-review-split">
        <section class="app-card document-pane">
          <div class="app-card-head">
            <h2>合同原文</h2>
            <span class="app-card-head-meta">{{ documentParagraphCount }} 段</span>
          </div>
          <div class="document-pane-body">
            <LegalDocumentViewer :content="documentContent" :highlighted-paragraph="highlightedParagraph" :highlighted-range="selectedRisk?.source_location" @paragraph-click="selectParagraph" />
          </div>
        </section>

        <aside class="app-card risk-pane">
          <div class="app-card-head">
            <h2>风险与问题</h2>
            <span class="app-card-head-meta risk-counts"><span v-if="evidenceChain.total" class="evidence-chain" :class="`is-${evidenceChain.level}`" :title="evidenceChainHint">{{ evidenceChain.anchored }}/{{ evidenceChain.total }} 有定位</span><span class="high-count">高 {{ riskCounts.high }}</span><span>中 {{ riskCounts.medium }}</span><span>低 {{ riskCounts.low }}</span></span>
          </div>
          <div class="risk-filters">
            <el-select v-model="riskFilter.level" size="small" clearable placeholder="全部等级">
              <el-option label="高风险" value="high" /><el-option label="中风险" value="medium" /><el-option label="低风险" value="low" />
            </el-select>
            <el-select v-model="riskFilter.clauseType" size="small" clearable placeholder="全部条款">
              <el-option v-for="type in availableClauseTypes" :key="type" :label="clauseLabel(type)" :value="type" />
            </el-select>
            <button
              v-if="evidenceChain.total"
              class="evidence-gap-toggle"
              :class="{ active: riskFilter.located === 'missing' }"
              type="button"
              :aria-pressed="riskFilter.located === 'missing'"
              :disabled="!evidenceChain.unanchored"
              @click="toggleEvidenceGap"
            >无定位 {{ evidenceChain.unanchored }}</button>
          </div>
          <div class="risk-list" aria-label="审查风险列表">
            <RiskIssueItem v-for="issue in filteredRisks" :key="issue.id || `${issue.clause_type}-${issue.source_location?.paragraph}`" :issue="issue" :active="selectedRisk === issue" :evidence="evidenceForRisk(evidenceChain, issue)" @select="selectRisk" />
            <div v-if="!filteredRisks.length" class="risk-empty">未发现符合筛选条件的风险项。</div>
          </div>

          <section v-if="selectedRisk" class="risk-detail">
            <p class="detail-kicker">{{ clauseLabel(selectedRisk.clause_type) || selectedRisk.label || '风险说明' }}</p>
            <p v-if="selectedEvidence && !selectedEvidence.anchored" class="evidence-note">无条款定位：建议人工定位到具体条款后再确认。</p>
            <p v-else-if="selectedEvidence?.snippetVerified === false" class="evidence-note is-warn">定位待核：引用的原文片段未在合同正文中匹配。</p>
            <h4>{{ selectedRisk.description || '需要进一步核对该条款。' }}</h4>
            <div class="detail-section anchor-section">
              <h5>条款定位</h5>
              <p v-if="selectedRisk.source_location?.paragraph" class="anchor-current">已定位：第 {{ selectedRisk.source_location.paragraph }} 段<span v-if="selectedRisk.source_location?.snippet">「{{ selectedRisk.source_location.snippet }}」</span></p>
              <template v-else>
                <p v-if="anchoringRisk === selectedRisk" class="anchor-hint">请在左侧合同原文中点击要锚定的段落（片段将取自该段原文）。<el-button size="small" text @click="cancelAnchoring">取消</el-button></p>
                <el-button v-else size="small" :loading="anchorLoading" @click="startAnchoring(selectedRisk)">从原文补定位</el-button>
              </template>
            </div>
            <div class="detail-section">
              <h5>修改建议</h5>
              <p>{{ selectedRisk.suggestion || '请结合交易背景补充具体修改方案。' }}</p>
              <el-button v-if="selectedRisk.suggestion" size="small" text @click="copySuggestionToRevision">加入修订稿参考</el-button>
            </div>
            <div v-if="selectedStructuredRisk" class="risk-disposition">
              <span>处理状态：{{ riskDispositionLabel(selectedStructuredRisk.status) }}</span>
              <div v-if="canResolveRisks" class="risk-disposition-actions">
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('accept')">接受风险</el-button>
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('mitigate')">已修改缓释</el-button>
                <el-button size="small" :disabled="isRiskResolved(selectedStructuredRisk)" @click="resolveSelectedRisk('dismiss')">忽略风险</el-button>
              </div>
              <p v-if="selectedStructuredRisk.resolution_note" class="resolution-note">{{ selectedStructuredRisk.resolution_note }}</p>
            </div>
            <div class="detail-section evidence-section">
              <h5>法律依据</h5>
              <EvidenceReference v-for="reference in selectedReferences" :key="reference.source_id || reference.title" :reference="reference" @open="openSourceDetail" />
              <p v-if="!selectedReferences.length" class="no-evidence">此风险项没有关联的法源引用。</p>
            </div>
          </section>

          <div class="human-decision-note">审查结果用于辅助判断，最终意见由律师确认。</div>
        </aside>
      </div>

      <div class="review-footnote">
        <AiOutputFeedback :target-type="'contract_review'" :target-id="contractResult.id" :value="contractResult.feedback_score" @submit="submitReviewFeedback" />
        <span v-if="quotaHint('review')" class="quota-note">{{ quotaHint('review') }}</span>
      </div>

      <!-- 折叠区（方向 B）：summary 是安静的一行，展开后是标准 .app-card -->
      <details class="contract-fold version-details">
        <summary>版本记录 <span>Version {{ contractResult.version || 1 }} · {{ contractVersions.length }} 条历史</span></summary>
        <section class="app-card fold-card">
          <div class="app-card-head">
            <h2>版本与修订</h2>
            <span class="app-card-head-meta">{{ contractVersions.length }} 条版本</span>
          </div>
          <div class="app-card-body">
            <DocumentVersion :versions="contractVersions" @set-base="setDiffBase" @set-target="setDiffTarget" />
            <div v-if="diffBase || diffTarget" class="contract-compare-summary">
              <span>对比起点：{{ diffBase ? `v${diffBase.version}` : '未选择' }}</span>
              <span>对比终点：{{ diffTarget ? `v${diffTarget.version}` : '当前版本' }}</span>
            </div>
            <DocumentDiff v-if="diffBase || diffTarget" :before="diffBase?.content || ''" :after="diffTarget?.content || contractResult.content || ''" :before-version="diffBase?.version" :after-version="diffTarget?.version" />
            <div v-if="revisionDraft !== null" class="revision-workspace">
              <div class="revision-heading"><strong>修订稿</strong><span>修改将进入下一审查版本，原审查结果和版本记录保留。</span></div>
              <el-input v-model="revisionDraft" type="textarea" :rows="10" maxlength="50000" show-word-limit aria-label="修订合同原文" />
              <el-button v-if="contractResult.status === 'returned_for_facts'" type="primary" size="small" :loading="resubmitLoading[contractResult.id]" @click="submitRevisionDraft">提交修订版本并重新审查</el-button>
              <el-button v-else-if="canSubmitForReview" size="small" @click="returnContractForRevision">提交审核并退回修改</el-button>
            </div>
            <div v-if="contractResult.status === 'returned_for_facts'" class="resubmit-editor">
              <span>修订从上方修订稿提交，系统保留上一版本并重新执行审查。</span>
            </div>
          </div>
        </section>
      </details>

      <details class="contract-fold history-details">
        <summary>历史审查 <span>{{ filteredContractReviews.length }} 条</span></summary>
        <section class="app-card fold-card">
          <div class="app-card-head">
            <h2>历史审查记录</h2>
            <span class="app-card-head-meta">{{ filteredContractReviews.length }} 条</span>
          </div>
          <div class="app-card-body">
            <div class="history-filters">
              <el-select v-model="reviewFilter.status" size="small" clearable placeholder="全部状态">
                <el-option label="待审核" value="pending_review" /><el-option label="需律师审查" value="needs_lawyer_review" />
                <el-option label="退回补充" value="returned_for_facts" /><el-option label="律师通过" value="lawyer_approved" />
                <el-option label="转线下" value="offline_handled" /><el-option label="已关闭" value="closed" />
              </el-select>
              <el-select v-model="reviewFilter.risk" size="small" clearable placeholder="全部风险等级">
                <el-option label="高风险" value="high" /><el-option label="中风险" value="medium" /><el-option label="低风险" value="low" />
              </el-select>
            </div>
            <QueryStateView :status="historyStatus" :error="historyError" empty-text="暂无历史审查记录。" min-height="80px" @retry="loadContractReviews">
              <div class="review-history-list">
                <details v-for="item in filteredContractReviews" :key="item.id" class="history-item" @toggle="loadHistoryVersions($event, item)">
                  <summary><strong>{{ item.title || `合同审查 #${item.id}` }}</strong><span class="app-tag">v{{ item.version || 1 }}</span><span class="app-tag" :class="statusTagClass(item.status)">{{ statusLabel(item.status) }}</span><span v-if="item.submitter_name" class="app-tag">{{ item.submitter_name }}</span><time>{{ formatDate(item.created_at) }}</time></summary>
                  <div class="history-item-body">
                    <div v-if="item.status === 'returned_for_facts'" class="resubmit-editor">
                      <label class="field-label" :for="`resubmit-history-${item.id}`">修订合同原文</label>
                      <el-input :id="`resubmit-history-${item.id}`" v-model="resubmitDraftForm[item.id]" type="textarea" :rows="5" placeholder="修改合同内容后重新提交。" />
                      <el-button size="small" type="primary" :loading="resubmitLoading[item.id]" @click="submitContractResubmit(item)">提交修订版本</el-button>
                    </div>
                    <DocumentVersion :versions="contractVersionMap[item.id] || []" />
                  </div>
                </details>
              </div>
            </QueryStateView>
          </div>
        </section>
      </details>
    </template>

    <!-- 跨审查风险统计：只读派生视图，展开时按当前作用域（本人 / 案件）拉取 -->
    <details class="contract-fold risk-stats-details" @toggle="onRiskStatsToggle">
      <summary>风险统计 <span v-if="riskStats">共 {{ riskStats.risks }} 条风险 · 高 {{ riskStats.high_total }}<template v-if="riskStatsDays"> · 近{{ riskStatsDays }}天</template></span><span v-else>跨审查汇总</span></summary>
      <section class="app-card fold-card">
        <div class="app-card-head">
          <h2>跨审查风险统计</h2>
          <span class="app-card-head-meta">{{ riskStats?.reviews || 0 }} 条审查 · {{ riskStats?.scope === 'case' ? '当前案件' : '本人记录' }}</span>
        </div>
        <div class="stats-range" role="group" aria-label="统计时间范围">
          <button
            v-for="option in RISK_STATS_RANGES" :key="option.label" type="button"
            class="stats-range-btn" :class="{ active: riskStatsDays === option.value }"
            @click="setRiskStatsRange(option.value)"
          >{{ option.label }}</button>
        </div>
        <div class="app-card-body">
          <p v-if="riskStatsLoading" class="stats-empty">正在汇总…</p>
          <p v-else-if="!riskStats || !riskStats.risks" class="stats-empty">暂无风险数据，完成一次合同审查后这里会汇总各条风险。</p>
          <template v-else>
            <div class="stats-grid">
              <div class="stat-tile"><span class="stat-value">{{ riskStats.risks }}</span><span class="stat-label">风险总数</span></div>
              <div class="stat-tile"><span class="stat-value">{{ riskStats.high_total }}</span><span class="stat-label">高/严重</span></div>
              <div class="stat-tile"><span class="stat-value" :class="{ 'is-warn': riskStats.pending_high > 0 }">{{ riskStats.pending_high }}</span><span class="stat-label">高风险待处理</span></div>
              <div class="stat-tile"><span class="stat-value" :class="{ 'is-warn': riskStats.unanchored > 0 }">{{ riskStats.unanchored }}</span><span class="stat-label">无条款定位</span></div>
              <div class="stat-tile"><span class="stat-value">{{ Math.round((riskStats.located_rate || 0) * 100) }}%</span><span class="stat-label">定位率</span></div>
            </div>
            <div class="severity-bars">
              <div v-for="level in ['critical', 'high', 'medium', 'low']" :key="level" class="severity-row">
                <span class="severity-name">{{ riskLabel(level) }}</span>
                <div class="severity-track"><div class="severity-fill" :class="`is-${level}`" :style="{ width: severityWidth(level) }" /></div>
                <span class="severity-count">{{ riskStats.by_severity[level] }}</span>
              </div>
            </div>
            <div v-if="riskStats.trend?.length" class="trend-chart">
              <div class="trend-heading">
                <span>风险趋势</span>
                <span class="trend-legend"><i class="trend-dot is-high" />高风险 <i class="trend-dot is-risk" />风险总数</span>
              </div>
              <div class="trend-bars">
                <div v-for="bucket in riskStats.trend" :key="bucket.week_start" class="trend-col" :title="`${bucket.week_start} 起：${bucket.risks} 条风险、${bucket.high} 条高/严重`">
                  <div class="trend-track">
                    <div class="trend-fill is-risk" :style="{ height: trendHeight(bucket.risks) }">
                      <div class="trend-fill is-high" :style="{ height: trendHighHeight(bucket) }" />
                    </div>
                  </div>
                  <span class="trend-label">{{ trendLabel(bucket.week_start) }}</span>
                </div>
              </div>
            </div>
            <div v-if="riskStats.by_clause_type?.length" class="clause-stats">
              <span class="clause-stats-label">高发条款</span>
              <span v-for="item in riskStats.by_clause_type" :key="item.clause_type" class="app-tag">{{ clauseLabel(item.clause_type) }} {{ item.count }}</span>
            </div>
          </template>
        </div>
      </section>
    </details>

    <details class="contract-fold comparison-details">
      <summary>合同版本对比</summary>
      <section class="app-card fold-card">
        <div class="app-card-head">
          <h2>版本对比录入</h2>
          <span class="app-card-head-meta">粘贴两份文本后对比</span>
        </div>
        <div class="app-card-body">
          <div class="compare-grid">
            <div><label class="field-label" for="contract-a-title">合同 A</label><el-input id="contract-a-title" v-model="compareForm.title_a" placeholder="合同名称" /><el-input v-model="compareForm.content_a" class="compare-input" type="textarea" :rows="6" placeholder="粘贴合同 A 原文" maxlength="50000" /></div>
            <div><label class="field-label" for="contract-b-title">合同 B</label><el-input id="contract-b-title" v-model="compareForm.title_b" placeholder="合同名称" /><el-input v-model="compareForm.content_b" class="compare-input" type="textarea" :rows="6" placeholder="粘贴合同 B 原文" maxlength="50000" /></div>
          </div>
          <div class="compare-actions"><el-button type="primary" :loading="compareLoading" @click="submitCompare">开始对比</el-button><el-button v-if="compareResult" @click="exportCompare">导出对比结果</el-button></div>
          <div v-if="compareResult" class="compare-result"><p>{{ compareResult.summary }}</p><div v-for="field in compareResult.fields || []" :key="field.label" class="compare-result-row"><strong>{{ field.label }}</strong><span>{{ field.value_a || '未提及' }}</span><span>{{ field.value_b || '未提及' }}</span><span>{{ field.note || (field.conflict ? '存在差异' : '一致') }}</span></div></div>
        </div>
      </section>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import 'element-plus/es/components/message-box/style/css'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/upload/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/button/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import LegalDocumentViewer from './LegalDocumentViewer.vue'
import RiskIssueItem from './RiskIssueItem.vue'
import EvidenceReference from './EvidenceReference.vue'
import DocumentVersion from './DocumentVersion.vue'
import DocumentDiff from './DocumentDiff.vue'
import { useContractReviews } from '../../composables/useContractReviews'
import { useContractRiskPresentation, clauseLabel, formatDate, riskLabel, statusLabel } from '../../composables/useLegalWorkspacePresentation'
import { EVIDENCE_CHAIN_LEVELS, evidenceForRisk, resolveEvidenceChain } from '../../composables/useContractEvidence'
import { useContractComparison } from '../../composables/useContractComparison'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import { useStagedProgress } from '../../composables/useStagedProgress'
import QueryStateView from '../QueryStateView.vue'

const props = defineProps({ caseId: { type: Number, default: null }, downloadText: { type: Function, default: null } })
const route = useRoute()
const router = useRouter()
const { quotaHint, loadQuota } = useQuota()
const { openSourceDetail } = useLegalSourceDetail()
const {
  contractForm, contractLoading, contractResult, contractReviews, reviewsQuery, uploadLoading, contractVersionMap,
  resubmitDraftForm, resubmitLoading, riskStats, riskStatsLoading, riskStatsDays, loadRiskStats,
  loadContractReviews, submitContractReview: runContractReview,
  onExpandContractReview, submitContractResubmit, handleContractUpload: uploadContractReview,
} = useContractReviews({ client: legalWorkspace, message: ElMessage, caseId: computed(() => props.caseId) })

// 风险统计：展开时才拉，避免每次进合同页都多打一个聚合接口
const RISK_STATS_RANGES = [
  { label: '近7天', value: 7 },
  { label: '近30天', value: 30 },
  { label: '全部', value: null },
]
const onRiskStatsToggle = (event) => {
  if (event.target.open) void loadRiskStats()
}
const setRiskStatsRange = (days) => {
  if (riskStatsDays.value === days) return
  void loadRiskStats(days)
}
const trendMax = computed(() => Math.max(1, ...(riskStats.value?.trend || []).map((bucket) => bucket.risks)))
const trendHeight = (risks) => `${Math.max(2, Math.round((risks / trendMax.value) * 48))}px`
const trendHighHeight = (bucket) => (bucket.risks ? `${Math.round((bucket.high / bucket.risks) * 100)}%` : '0%')
const trendLabel = (weekStart) => String(weekStart || '').slice(5)
const severityWidth = (level) => {
  const total = riskStats.value?.risks || 0
  if (!total) return '0%'
  return `${Math.round(((riskStats.value?.by_severity?.[level] || 0) / total) * 100)}%`
}

// LLM 审查约需 20-30 秒，阶段性提示缓解等待焦虑（ux-audit M-6）
const contractStageText = useStagedProgress(contractLoading, [
  '正在解析合同条款…',
  '正在逐条识别风险与缺失义务…',
  '正在生成审查建议…',
])
const {
  reviewFilter, riskFilter, highlightedParagraph, availableClauseTypes, filteredRisks, filteredContractReviews,
  contractParagraphs, resetRiskFilter, jumpToRisk,
} = useContractRiskPresentation({ contractForm, contractResult, contractReviews })
const { compareForm, compareLoading, compareResult, submitCompare } = useContractComparison({ client: legalWorkspace, message: ElMessage })
// 历史审查三态（ux-audit P1-3/M-13）：加载失败与"暂无记录"分离，失败可重试
const historyStatus = computed(() => {
  if (reviewsQuery.isLoading.value) return 'loading'
  if (reviewsQuery.isError.value) return 'error'
  if (!filteredContractReviews.value.length) return 'empty'
  return 'success'
})
const historyError = computed(() => (reviewsQuery.isError.value ? reviewsQuery.error.value : null))
const selectedRisk = ref(null)
const structuredRiskItems = ref([])
const riskActionLoading = ref(false)
const revisionDraft = ref(null)
const diffBase = ref(null)
const diffTarget = ref(null)
const canResolveRisks = computed(() => structuredRiskItems.value.some((item) => item.can_resolve))
const selectedStructuredRisk = computed(() => {
  if (!selectedRisk.value) return null
  // 风险项由后端从 risks_json 按下标派生（id = 下标 + 1），优先按下标匹配，避免内容重复时错配
  const index = (contractResult.value?.risks || []).indexOf(selectedRisk.value)
  const byIndex = structuredRiskItems.value.find((item) => item.index === index)
  if (byIndex) return byIndex
  return structuredRiskItems.value.find((item) => item.id === selectedRisk.value.id
    || item.original_text_excerpt === selectedRisk.value.source_location?.snippet
    || (item.category === selectedRisk.value.clause_type && item.summary === selectedRisk.value.description)) || null
})
const riskDispositionLabel = (status) => ({ open: '待处理', needs_review: '待审核', accepted: '已接受', mitigated: '已缓释', dismissed: '已忽略' }[status] || status || '待处理')
// 审查状态 → .app-tag 语义变体（方向 B 标签统一浅底深字，不用 Element 实心色块）
const statusTagClass = (status) => ({
  lawyer_approved: 'is-ok', offline_handled: 'is-info',
  pending_review: 'is-pending', needs_lawyer_review: 'is-pending', returned_for_facts: 'is-pending',
}[status] || '')
const isRiskResolved = (item) => ['accepted', 'mitigated', 'dismissed'].includes(item?.status)
const reviewSubmitting = ref(false)
const documentContent = computed(() => contractResult.value?.content || contractForm.value.content || '')
const documentParagraphCount = computed(() => documentContent.value.split('\n').filter((line) => line.trim()).length)
const contractVersions = computed(() => contractVersionMap.value[contractResult.value?.id] || [])
const riskCounts = computed(() => (contractResult.value?.risks || []).reduce((counts, risk) => {
  if (Object.hasOwn(counts, risk.risk_level)) counts[risk.risk_level] += 1
  return counts
}, { high: 0, medium: 0, low: 0 }))
// 证据链：与风险清单同源实时计算，徽标/筛选/详情提示共用一份判定
const evidenceChain = computed(() => resolveEvidenceChain(
  contractResult.value?.risks || [],
  contractResult.value?.evidence_summary || null,
  documentContent.value,
))
const selectedEvidence = computed(() => (selectedRisk.value ? evidenceForRisk(evidenceChain.value, selectedRisk.value) : null))
const evidenceChainHint = computed(() => {
  const meta = EVIDENCE_CHAIN_LEVELS[evidenceChain.value.level] || EVIDENCE_CHAIN_LEVELS.none
  const snippet = evidenceChain.value.snippets
    ? `｜snippet 可核验 ${evidenceChain.value.verified}/${evidenceChain.value.snippets}`
    : '｜没有 snippet 可核验'
  return `${meta.label} · ${meta.hint}${snippet}`
})
const toggleEvidenceGap = () => {
  riskFilter.value = { ...riskFilter.value, located: riskFilter.value.located === 'missing' ? '' : 'missing' }
}
const selectedReferences = computed(() => selectedRisk.value?.references || selectedRisk.value?.legal_basis || contractResult.value?.references || [])
const canSubmitForReview = computed(() => Boolean(contractResult.value?.id) && !['pending_review', 'needs_lawyer_review', 'lawyer_approved'].includes(contractResult.value?.status))

watch(() => contractResult.value?.risks, (risks) => { selectedRisk.value = risks?.[0] || null }, { deep: true })
// 把「当前正在审查的合同」写进 URL：右栏助手读 URL 就知道用户在看哪一份，
// 刷新或直达也能回到同一份（与咨询 tab 的 consultation_id 同一套做法）。
watch(() => contractResult.value?.id, (id) => {
  const current = Number(route.query.contract_id) || null
  if ((id ? Number(id) : null) === current) return
  router.replace({ query: { ...route.query, contract_id: id ? String(id) : undefined } })
})
watch(() => contractResult.value?.id, (id) => { if (id) onExpandContractReview({ id }) })
watch(() => contractResult.value?.id, async (id) => {
  structuredRiskItems.value = []
  if (!id) return
  try {
    const { data } = await legalWorkspace.listContractRiskItems(id)
    structuredRiskItems.value = data || []
  } catch {
    structuredRiskItems.value = []
  }
})

const submitContractReview = async () => {
  await runContractReview(resetRiskFilter)
  await loadQuota()
}
const handleContractUpload = (file) => uploadContractReview(file, resetRiskFilter)
const selectRisk = (risk) => {
  selectedRisk.value = risk
  jumpToRisk(risk)
}
const copySuggestionToRevision = async () => {
  if (!selectedRisk.value?.suggestion) return
  revisionDraft.value = revisionDraft.value === null ? documentContent.value : revisionDraft.value
  const suggestion = `\n\n【修订参考：${selectedRisk.value.label || '风险条款'}】\n${selectedRisk.value.suggestion}`
  if (!revisionDraft.value.includes(suggestion)) revisionDraft.value += suggestion
  ElMessage.success('已加入修订稿参考')
}
const resolveSelectedRisk = async (action) => {
  if (!contractResult.value?.id || !selectedStructuredRisk.value || riskActionLoading.value) return
  riskActionLoading.value = true
  try {
    const { data } = await legalWorkspace.updateContractRiskItem(contractResult.value.id, selectedStructuredRisk.value.id, { action, note: '合同审查工作区处理' })
    structuredRiskItems.value = structuredRiskItems.value.map((item) => item.id === data.id ? data : item)
    ElMessage.success('风险项状态已更新')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || error.response?.data?.detail || '风险项处理失败')
  } finally {
    riskActionLoading.value = false
  }
}
const setDiffBase = (version) => { diffBase.value = version }
const setDiffTarget = (version) => { diffTarget.value = version }
const submitRevisionDraft = async () => {
  if (!contractResult.value || !revisionDraft.value?.trim()) return ElMessage.warning('请输入修订后的合同内容')
  resubmitDraftForm.value = { ...resubmitDraftForm.value, [contractResult.value.id]: revisionDraft.value }
  await submitContractResubmit(contractResult.value)
  revisionDraft.value = null
  await loadContractReviews()
  contractResult.value = contractReviews.value.find((item) => item.id === contractResult.value.id) || contractResult.value
}
const returnContractForRevision = async () => {
  if (!contractResult.value?.id) return
  try {
    await legalWorkspace.submitLegalReviewAction('contract_review', contractResult.value.id, { action: 'return', note: '请在合同工作区补充修订' })
    contractResult.value.status = 'returned_for_facts'
    revisionDraft.value = documentContent.value
    ElMessage.success('已退回修订')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || error.response?.data?.detail || '退回修订失败')
  }
}
const anchoringRisk = ref(null)
const anchorLoading = ref(false)
const startAnchoring = (risk) => {
  anchoringRisk.value = risk
  highlightedParagraph.value = null
}
const cancelAnchoring = () => { anchoringRisk.value = null }
const saveAnchor = async (paragraph) => {
  const risk = anchoringRisk.value
  const index = (contractResult.value?.risks || []).indexOf(risk)
  if (!risk || index < 0) return
  const text = contractParagraphs.value[paragraph - 1] || ''
  if (!text) return
  anchorLoading.value = true
  try {
    const response = await legalWorkspace.anchorContractRisk(contractResult.value.id, index, {
      paragraph,
      snippet: text.slice(0, 200),
    })
    const updated = response?.data || response
    if (updated?.risks) {
      contractResult.value = updated
      selectedRisk.value = updated.risks[index] || null
    }
    anchoringRisk.value = null
    ElMessage.success(`已标记为第 ${paragraph} 段，证据链已更新`)
  } catch (error) {
    ElMessage.error(error.response?.data?.message || error.message || '补定位失败')
  } finally {
    anchorLoading.value = false
  }
}
const selectParagraph = (paragraph) => {
  if (anchoringRisk.value) { void saveAnchor(paragraph); return }
  const risk = (contractResult.value?.risks || []).find((item) => item.source_location?.paragraph === paragraph)
  if (risk) selectedRisk.value = risk
}
const loadHistoryVersions = (event, item) => {
  if (event.target.open) onExpandContractReview(item)
}
const startNewReview = () => {
  contractResult.value = null
  contractForm.value = { title: '', content: '' }
  resetRiskFilter()
  structuredRiskItems.value = []
  revisionDraft.value = null
  diffBase.value = null
  diffTarget.value = null
}
const submitForLawyerReview = async () => {
  if (!contractResult.value?.id || reviewSubmitting.value) return
  try {
    await ElMessageBox.confirm('提交后将进入律师审核队列，由审核律师复核并作出最终决定。', '提交律师审核', {
      confirmButtonText: '确认提交', cancelButtonText: '取消', type: 'info',
    })
    reviewSubmitting.value = true
    await legalWorkspace.submitLegalReviewAction('contract_review', contractResult.value.id, { action: 'submit_review', note: '用户提交律师审核' })
    contractResult.value.status = 'needs_lawyer_review'
    await loadContractReviews()
    ElMessage.success('已提交律师审核')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') ElMessage.error(error.response?.data?.detail || '提交审核失败')
  } finally {
    reviewSubmitting.value = false
  }
}

const exportReview = () => {
  if (!contractResult.value || !props.downloadText) return
  const result = contractResult.value
  const lines = [`# 合同审查意见书`, '', `**合同标题：** ${result.title || contractForm.value.title || '未命名'}`, `**审查状态：** ${statusLabel(result.status)}`, `**版本：** v${result.version || 1}`, '', '## 审查摘要', result.summary || '', '', '## 条款风险明细']
  if (result.risks?.length) result.risks.forEach((risk, index) => {
    lines.push(`### ${index + 1}. ${risk.label || clauseLabel(risk.clause_type)}（${riskLabel(risk.risk_level)}）`)
    lines.push(`- 风险说明：${risk.description || '无'}`, `- 修改建议：${risk.suggestion || '无'}`)
    if (risk.source_location?.snippet) lines.push(`- 原文：${risk.source_location.snippet}`)
    lines.push('')
  })
  else lines.push('未识别到条款风险。')
  lines.push('', '## 法律依据')
  ;(result.references || []).forEach((reference) => lines.push(`- ${reference.title || '法源'} ${reference.citation || ''} ${reference.version ? `（版本 ${reference.version}）` : ''}`))
  lines.push('', '---', '*辅助审查结果，最终意见由律师确认。*')
  props.downloadText(`${result.title || '合同审查意见书'}.md`, lines.join('\n'))
}

const exportCompare = () => {
  if (!compareResult.value || !props.downloadText) return
  const result = compareResult.value
  const lines = ['# 合同对比结果', '', `**合同 A：** ${compareForm.value.title_a || '合同 A'}`, `**合同 B：** ${compareForm.value.title_b || '合同 B'}`, '', result.summary || '', '', '## 差异明细']
  ;(result.fields || []).forEach((field) => lines.push(`- ${field.label}：A ${field.value_a || '未提及'}；B ${field.value_b || '未提及'}；${field.note || (field.conflict ? '存在差异' : '一致')}`))
  props.downloadText('合同对比结果.md', lines.join('\n'))
}

const submitReviewFeedback = async (score, note) => {
  if (!contractResult.value?.id) return
  try {
    await legalWorkspace.submitReviewFeedback(contractResult.value.id, { score, note })
    contractResult.value.feedback_score = score
    ElMessage.success('反馈已提交')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
  }
}

const openLatestContractReview = async () => {
  await loadContractReviews()
  if (contractResult.value || !contractReviews.value.length) return
  // 刷新或从别处带着 contract_id 进来时，回到同一份合同（右栏助手据此把这份合同交给后端）
  const requestedId = Number(route.query.contract_id) || null
  const requested = requestedId ? contractReviews.value.find((row) => Number(row.id) === requestedId) : null
  const latest = requested || [...contractReviews.value].sort((a, b) => {
    const left = new Date(a.updated_at || a.created_at || 0).getTime() || Number(a.id) || 0
    const right = new Date(b.updated_at || b.created_at || 0).getTime() || Number(b.id) || 0
    return right - left
  })[0]
  contractResult.value = latest
  contractForm.value = { title: latest.title || '', content: latest.content || '' }
  revisionDraft.value = latest.status === 'returned_for_facts' ? latest.content || '' : null
  await onExpandContractReview(latest)
}

onMounted(openLatestContractReview)
</script>

<style scoped>
/* 方向 B：间距/圆角/色板一律走 design-tokens 令牌，组件内只做排版补充 */
.contract-workspace { display: grid; gap: var(--space-4); min-width: 0; }

/* 录入态 */
.contract-intake { min-width: 0; }
.intake-body { display: grid; gap: var(--space-2); }
.field-label { margin: var(--space-1) 0 0; color: var(--color-text-secondary); font-size: var(--text-xs); font-weight: 600; }
.intake-actions { display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-2); padding-top: var(--space-2); }
.staged-hint { flex-basis: 100%; margin-top: 2px; color: var(--color-text-muted); font-size: var(--text-xs); text-align: right; }

/* 结果态页头 */
.document-heading { display: flex; align-items: center; gap: var(--space-3); min-width: 0; }
.back-action { width: 32px; height: 32px; flex: none; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-surface); color: var(--color-text-secondary); font-size: 21px; line-height: 1; cursor: pointer; transition: var(--transition-fast); }
.back-action:hover { border-color: var(--color-border-hover); color: var(--color-text); }
.document-title-group { min-width: 0; }
.document-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.document-meta { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.document-time { color: var(--color-text-muted); font-size: var(--text-xs); }

/* 审查摘要 */
.review-summary-text { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.75; }

/* 合同原文 / 风险双栏 */
.contract-review-split { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(340px, 0.8fr); gap: var(--space-4); min-height: 680px; }
.document-pane, .risk-pane { min-width: 0; }
.document-pane { display: grid; grid-template-rows: auto minmax(0, 1fr); }
.document-pane-body { min-height: 0; overflow: hidden; }
/* 风险栏用 flex 列：风险项详情可长可短（未选中时不渲染），弹性 + 独立滚动避免卡片 overflow:hidden 裁切 */
.risk-pane { display: flex; flex-direction: column; max-height: 860px; }
.risk-filters, .human-decision-note { flex: none; }
.risk-counts { display: flex; align-items: center; gap: var(--space-2); white-space: nowrap; }
.evidence-chain { padding: 2px 8px; border: 1px solid var(--color-border-light); border-radius: var(--radius-full); color: var(--color-text-secondary); font-size: var(--text-xs); font-weight: 500; }
.evidence-chain.is-strong { border-color: var(--color-success); color: var(--color-success); }
.evidence-chain.is-partial { border-color: var(--color-warning); color: var(--color-warning); }
.evidence-chain.is-weak, .evidence-chain.is-none { border-color: var(--color-danger); color: var(--color-danger); }
.evidence-gap-toggle { flex: none; padding: 0 var(--space-3); height: 24px; border: 1px solid var(--color-border-light); border-radius: var(--radius-full); background: var(--color-surface); color: var(--color-text-secondary); font-size: var(--text-xs); cursor: pointer; }
.evidence-gap-toggle:hover:not(:disabled) { border-color: var(--color-warning); color: var(--color-warning); }
.evidence-gap-toggle.active { border-color: var(--color-warning); background: var(--color-warning-light); color: var(--color-warning); }
.evidence-gap-toggle:disabled { opacity: 0.5; cursor: default; }
.evidence-note { margin: 0 0 var(--space-2); padding: 6px 8px; border-radius: var(--radius-sm); background: var(--color-warning-light); color: var(--color-warning); font-size: var(--text-xs); line-height: 1.6; }
.evidence-note.is-warn { background: var(--color-danger-light); color: var(--color-danger); }
.risk-counts .high-count { color: var(--color-danger); }
.risk-filters { display: flex; gap: var(--space-2); padding: var(--space-3) var(--space-5); border-bottom: 1px solid var(--color-border-light); }
.risk-filters .el-select { width: 50%; min-width: 0; }
.risk-list { flex: 1 1 auto; min-height: 120px; overflow-y: auto; border-bottom: 1px solid var(--color-border-light); }
.risk-empty, .no-evidence { padding: var(--space-4) var(--space-5); color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.6; }
.risk-detail { flex: 0 1 auto; min-height: 0; overflow-y: auto; padding: var(--space-4) var(--space-5); }
.detail-kicker { margin: 0 0 var(--space-1); color: var(--color-text-muted); font-size: var(--text-xs); }
.risk-detail h4 { margin: 0 0 var(--space-4); color: var(--color-text); font-size: var(--text-sm); line-height: 1.65; font-weight: 600; }
.detail-section + .detail-section { margin-top: var(--space-4); }
.detail-section h5 { margin: 0 0 var(--space-1); color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 600; }
.detail-section p { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.65; white-space: pre-line; }
.detail-section .el-button { margin-top: var(--space-2); padding-left: 0; }
.evidence-section { padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
.evidence-section .no-evidence { padding: var(--space-1) 0; }
.human-decision-note { padding: var(--space-3) var(--space-5); border-top: 1px solid var(--color-border-light); color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.5; }
.risk-disposition { display: grid; gap: var(--space-2); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); color: var(--color-text-secondary); font-size: var(--text-xs); }
.risk-disposition-actions { display: flex; flex-wrap: wrap; gap: var(--space-2); }
.resolution-note { margin: 0; color: var(--color-text-muted); font-size: var(--text-xs); }

/* 反馈行与折叠区 */
.review-footnote { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: var(--space-3); }
.quota-note { color: var(--color-text-muted); font-size: var(--text-xs); }
.contract-fold > summary { display: flex; align-items: baseline; gap: var(--space-2); padding: var(--space-3) var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); background: var(--color-surface); color: var(--color-text-secondary); font-size: var(--text-sm); font-weight: 600; cursor: pointer; list-style: none; transition: var(--transition-fast); }
.contract-fold > summary::-webkit-details-marker { display: none; }
.contract-fold > summary::before { color: var(--color-text-muted); content: '▸'; transition: var(--transition-fast); }
.contract-fold[open] > summary::before { transform: rotate(90deg); }
.contract-fold > summary:hover { border-color: var(--color-border-hover); color: var(--color-text); }
.contract-fold > summary span { color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 400; }
.fold-card { margin-top: var(--space-3); }
.contract-compare-summary { display: flex; flex-wrap: wrap; gap: var(--space-3); margin-bottom: var(--space-3); color: var(--color-text-muted); font-size: var(--text-xs); }
.revision-workspace, .resubmit-editor { display: grid; gap: var(--space-2); margin-top: var(--space-4); padding-top: var(--space-3); border-top: 1px solid var(--color-border-light); }
.revision-heading { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--space-2); }
.revision-heading strong { color: var(--color-text); font-size: var(--text-sm); }
.revision-heading span { color: var(--color-text-muted); font-size: var(--text-xs); }
.history-filters { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-3); }
.history-item { border-bottom: 1px solid var(--color-border-light); }
.history-item:last-child { border-bottom: 0; }
.history-item > summary { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); padding: var(--space-3) var(--space-1); color: var(--color-text-secondary); font-size: var(--text-sm); cursor: pointer; }
.history-item > summary strong { flex: 1; min-width: 140px; color: var(--color-text); font-weight: 600; }
.history-item > summary time { color: var(--color-text-muted); font-size: var(--text-xs); }
.history-item-body { padding: 0 var(--space-2) var(--space-4); }
.compare-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-4); }
.compare-grid > div { display: grid; gap: var(--space-2); align-content: start; }
.compare-input { margin-top: var(--space-1); }
.compare-actions { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-3); }
.compare-result { margin-top: var(--space-4); }
.compare-result p { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.6; }
.compare-result-row { display: grid; grid-template-columns: 100px repeat(3, minmax(0, 1fr)); gap: var(--space-2); padding: var(--space-2) var(--space-1); border-bottom: 1px solid var(--color-border-light); font-size: var(--text-xs); }
.compare-result-row strong { color: var(--color-text); }
.compare-result-row span { color: var(--color-text-secondary); overflow-wrap: anywhere; }
@media (max-width: 940px) {
  .contract-review-split { grid-template-columns: minmax(0, 1fr); }
  .document-pane { min-height: 480px; }
  .risk-pane { max-height: none; }
  .risk-list { min-height: 200px; }
}
@media (max-width: 640px) {
  .document-heading { align-items: flex-start; }
  .contract-review-split { min-height: 0; }
  .document-pane { min-height: 420px; }
  .risk-filters { align-items: stretch; flex-direction: column; }
  .risk-filters .el-select { width: 100%; }
  .risk-counts { gap: var(--space-1); }
  .compare-grid { grid-template-columns: minmax(0, 1fr); }
  .compare-result-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
}

.anchor-current { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); line-height: 1.6; }
.anchor-hint { display: flex; align-items: center; gap: var(--space-2); margin: 0; padding: 6px 8px; border-radius: var(--radius-sm); background: var(--color-primary-light); color: var(--color-text-secondary); font-size: var(--text-xs); line-height: 1.6; }

/* 跨审查风险统计 */
.stats-empty { margin: 0; color: var(--color-text-secondary); font-size: var(--text-xs); line-height: 1.7; }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(84px, 1fr)); gap: var(--space-2); margin-bottom: var(--space-3); }
.stat-tile { display: flex; flex-direction: column; gap: 2px; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: var(--color-bg-subtle); }
.stat-value { font-size: var(--text-lg); font-weight: 600; font-variant-numeric: tabular-nums; color: var(--color-text-primary); line-height: 1.2; }
.stat-value.is-warn { color: var(--color-warning); }
.stat-label { font-size: var(--text-xs); color: var(--color-text-secondary); }
.severity-bars { display: grid; gap: 6px; margin-bottom: var(--space-3); }
.severity-row { display: grid; grid-template-columns: 52px 1fr 32px; align-items: center; gap: var(--space-2); }
.severity-name { font-size: var(--text-xs); color: var(--color-text-secondary); }
.severity-track { height: 6px; border-radius: 3px; background: var(--color-bg-subtle); overflow: hidden; }
.severity-fill { height: 100%; border-radius: 3px; background: var(--color-primary); transition: width 0.2s ease; }
.severity-fill.is-critical { background: var(--color-danger); }
.severity-fill.is-high { background: var(--color-warning); }
.severity-fill.is-medium { background: var(--color-primary); }
.severity-fill.is-low { background: var(--color-text-tertiary); }
.severity-count { font-size: var(--text-xs); color: var(--color-text-secondary); text-align: right; font-variant-numeric: tabular-nums; }
.clause-stats { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); }
.clause-stats-label { font-size: var(--text-xs); color: var(--color-text-secondary); }

.stats-range { display: inline-flex; gap: 2px; padding: 2px; margin: 0 var(--space-3) var(--space-2); border: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: var(--color-bg-subtle); width: fit-content; }
.stats-range-btn { padding: 2px 10px; border: 0; border-radius: calc(var(--radius-sm) - 2px); background: transparent; color: var(--color-text-secondary); font-size: var(--text-xs); cursor: pointer; }
.stats-range-btn:hover { color: var(--color-text-primary); }
.stats-range-btn.active { background: var(--color-bg-elevated); color: var(--color-text-primary); font-weight: 600; box-shadow: inset 0 0 0 1px var(--color-border-light); }
.trend-chart { margin-bottom: var(--space-3); }
.trend-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-2); font-size: var(--text-xs); color: var(--color-text-secondary); }
.trend-legend { display: inline-flex; align-items: center; gap: 4px; }
.trend-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-left: 8px; }
.trend-dot.is-high { background: var(--color-danger); }
.trend-dot.is-risk { background: var(--color-primary); }
.trend-bars { display: flex; align-items: flex-end; gap: var(--space-2); height: 64px; }
.trend-col { display: flex; flex: 1; flex-direction: column; align-items: center; justify-content: flex-end; gap: 4px; min-width: 0; }
.trend-track { display: flex; align-items: flex-end; width: 100%; height: 48px; }
.trend-fill { width: 100%; border-radius: 2px 2px 0 0; }
.trend-fill.is-risk { background: color-mix(in srgb, var(--color-primary) 55%, transparent); }
.trend-fill.is-high { display: flex; align-items: flex-start; background: var(--color-danger); }
.trend-label { font-size: 10px; color: var(--color-text-tertiary); white-space: nowrap; }
</style>
