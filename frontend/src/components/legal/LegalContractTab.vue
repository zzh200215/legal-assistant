<template>
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">合同智能审查</span></template>
            <el-form @submit.prevent="submitContractReview">
              <el-form-item label="合同标题">
                <el-input v-model="contractForm.title" placeholder="例如：技术服务合同" />
              </el-form-item>
              <el-form-item label="合同内容">
                <el-input v-model="contractForm.content" type="textarea" :rows="8" placeholder="粘贴合同全文或主要条款..." maxlength="50000" show-word-limit />
              </el-form-item>
              <div class="upload-row">
                <el-upload
                  :show-file-list="false"
                  :before-upload="handleContractUpload"
                  accept=".pdf,.docx,.doc,.txt,.md"
                >
                  <el-button :loading="uploadLoading" :icon="Upload">上传合同文件（PDF/DOCX/TXT）</el-button>
                </el-upload>
                <span class="muted">或直接粘贴文本后点击审查</span>
              </div>
              <el-button type="primary" :loading="contractLoading" @click="submitContractReview" style="margin-top:12px">开始审查</el-button>
            </el-form>
          </el-card>

          <div v-if="contractResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">审查意见</span>
                  <el-tag v-if="quotaHint('review')" size="small" effect="plain" :type="quotaSummary?.review?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('review') }}</el-tag>
                  <el-tag :type="contractResult.status === 'needs_lawyer_review' ? 'danger' : 'warning'" size="small">{{ statusLabel(contractResult.status) }}</el-tag>
                  <el-tag v-if="contractResult.confidence !== undefined" :type="confidenceTagType(contractResult.confidence)" size="small" effect="plain">置信度 {{ contractResult.confidence }}%</el-tag>
                  <el-button size="small" @click="exportReview" style="margin-left:auto">导出意见书</el-button>
                </div>
              </template>
              <p class="summary-text">{{ contractResult.summary }}</p>

              <el-divider content-position="left">合同原文</el-divider>
              <div ref="contractContentRef" class="contract-content">
                <pre v-for="(para, idx) in contractParagraphs" :key="idx" :id="`para-${idx + 1}`" class="contract-paragraph" :class="{ highlighted: highlightedParagraph === idx + 1 }">{{ para }}</pre>
              </div>

              <el-divider content-position="left">风险明细</el-divider>
              <div class="filter-row">
                <el-select v-model="riskFilter.clauseType" placeholder="按条款类型筛选" clearable size="small" style="width:160px">
                  <el-option v-for="ct in availableClauseTypes" :key="ct" :label="clauseLabel(ct)" :value="ct" />
                </el-select>
                <el-select v-model="riskFilter.level" placeholder="按风险等级筛选" clearable size="small" style="width:140px">
                  <el-option label="高风险" value="high" />
                  <el-option label="中风险" value="medium" />
                  <el-option label="低风险" value="low" />
                </el-select>
                <el-select v-model="riskFilter.sortBy" placeholder="排序方式" clearable size="small" style="width:140px">
                  <el-option label="按风险等级降序" value="risk_desc" />
                  <el-option label="按段落顺序" value="paragraph_asc" />
                </el-select>
              </div>
              <el-table :data="filteredRisks" stripe size="small" style="margin-top:12px" @row-click="jumpToRisk">
                <el-table-column prop="label" label="条款类型" width="120">
                  <template #default="{ row }">{{ clauseLabel(row.clause_type) || row.label }}</template>
                </el-table-column>
                <el-table-column prop="risk_level" label="风险等级" width="100">
                  <template #default="{ row }">
                    <el-tag :type="riskTagType(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="description" label="风险说明" show-overflow-tooltip />
                <el-table-column prop="suggestion" label="修改建议" show-overflow-tooltip />
                <el-table-column label="原文定位" width="200">
                  <template #default="{ row }">
                    <el-button v-if="row.source_location?.paragraph" size="small" type="primary" link @click.stop="jumpToRisk(row)">
                      <el-icon><LocationInformation /></el-icon>
                      第 {{ row.source_location.paragraph }} 段
                    </el-button>
                    <span v-else class="muted">—</span>
                  </template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'open' ? 'warning' : 'info'" size="small">{{ row.status === 'open' ? '待处理' : '待补充' }}</el-tag>
                  </template>
                </el-table-column>
              </el-table>

              <el-divider content-position="left">参考依据</el-divider>
              <div v-if="contractResult.references?.length" class="reference-list">
                <div v-for="r in contractResult.references" :key="r.source_id" class="ref-item">
                  <el-button size="small" link type="primary" @click="openSourceDetail(r)">
                    <span class="ref-title">{{ r.title }}</span>
                  </el-button>
                  <span class="ref-citation">{{ r.citation }}</span>
                  <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                  <el-tag v-if="r.verification" :type="verificationTagType(r.verification)" size="small" effect="plain" class="verification-tag">{{ r.verification.verification_note }}</el-tag>
                  <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                </div>
              </div>
              <span v-else class="muted">暂无参考依据</span>

              <AiOutputFeedback :target-type="'contract_review'" :target-id="contractResult.id" :value="contractResult.feedback_score" @submit="submitReviewFeedback" />
            </el-card>
          </div>

          <el-card v-if="contractReviews.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史审查</span></template>
            <div class="filter-row">
              <el-select v-model="reviewFilter.status" placeholder="按状态筛选" clearable size="small" style="width:160px">
                <el-option label="待审核" value="pending_review" />
                <el-option label="需律师审查" value="needs_lawyer_review" />
                <el-option label="退回补充" value="returned_for_facts" />
                <el-option label="律师通过" value="lawyer_approved" />
                <el-option label="转线下" value="offline_handled" />
                <el-option label="已关闭" value="closed" />
              </el-select>
              <el-select v-model="reviewFilter.risk" placeholder="按风险筛选" clearable size="small" style="width:140px">
                <el-option label="高风险" value="high" />
                <el-option label="中风险" value="medium" />
                <el-option label="低风险" value="low" />
              </el-select>
            </div>
            <el-table :data="filteredContractReviews" stripe size="small" style="margin-top:12px" row-key="id" @expand-change="onExpandContractReview">
              <el-table-column type="expand">
                <template #default="{ row }">
                  <div class="version-panel">
                    <div v-if="row.status === 'returned_for_facts'" class="resubmit-form">
                      <strong>该记录已被退回，可修改后重新提交：</strong>
                      <el-input v-model="resubmitDraftForm[row.id]" type="textarea" :rows="4" placeholder="修改合同内容后重新提交..." style="margin-top:8px" />
                      <el-button size="small" type="primary" :loading="resubmitLoading[row.id]" @click="submitContractResubmit(row)" style="margin-top:8px">重新提交</el-button>
                    </div>
                    <div class="version-history">
                      <strong>历史版本：</strong>
                      <div v-if="contractVersionMap[row.id]?.length" class="version-list">
                        <div v-for="v in contractVersionMap[row.id]" :key="v.id" class="version-entry">
                          <el-tag size="small">v{{ v.version }}</el-tag>
                          <span class="version-time">{{ formatDate(v.created_at) }}</span>
                          <span class="version-status">{{ statusLabel(v.status_at_snapshot) }}</span>
                          <pre class="version-content">{{ v.content }}</pre>
                        </div>
                      </div>
                      <el-empty v-else description="暂无历史版本" :image-size="48" />
                    </div>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="title" label="合同" show-overflow-tooltip />
              <el-table-column label="版本" width="80">
                <template #default="{ row }">v{{ row.version || 1 }}</template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <el-card shadow="never" class="history-card">
            <template #header>
              <div class="result-header">
                <span class="card-title">合同冲突核对</span>
                <el-tag size="small" type="info">日期 / 金额 / 责任方 / 交付条件</el-tag>
              </div>
            </template>
            <el-form @submit.prevent="submitCompare">
              <div class="compare-grid">
                <div class="compare-col">
                  <el-form-item label="合同A标题">
                    <el-input v-model="compareForm.title_a" placeholder="例如：技术服务合同" />
                  </el-form-item>
                  <el-form-item label="合同A内容">
                    <el-input v-model="compareForm.content_a" type="textarea" :rows="6" placeholder="粘贴合同A全文或主要条款..." maxlength="50000" />
                  </el-form-item>
                </div>
                <div class="compare-col">
                  <el-form-item label="合同B标题">
                    <el-input v-model="compareForm.title_b" placeholder="例如：补充协议" />
                  </el-form-item>
                  <el-form-item label="合同B内容">
                    <el-input v-model="compareForm.content_b" type="textarea" :rows="6" placeholder="粘贴合同B全文或主要条款..." maxlength="50000" />
                  </el-form-item>
                </div>
              </div>
              <el-button type="primary" :loading="compareLoading" @click="submitCompare">开始核对</el-button>
            </el-form>
          </el-card>

          <div v-if="compareResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">核对结果</span>
                  <el-tag :type="compareResult.conflict_count > 0 ? 'danger' : 'success'" size="small">{{ compareResult.conflict_count }} 项差异</el-tag>
                  <el-button size="small" @click="exportCompare" style="margin-left:auto">导出核对报告</el-button>
                </div>
              </template>
              <p class="summary-text">{{ compareResult.summary }}</p>
              <el-table :data="compareResult.fields" stripe size="small" style="margin-top:16px">
                <el-table-column prop="label" label="字段" width="120" />
                <el-table-column label="合同A" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.value_a }}</template>
                </el-table-column>
                <el-table-column label="合同B" show-overflow-tooltip>
                  <template #default="{ row }">{{ row.value_b }}</template>
                </el-table-column>
                <el-table-column label="差异" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.conflict ? 'danger' : 'success'" size="small">{{ row.conflict ? '不一致' : '一致' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="级别" width="80">
                  <template #default="{ row }">
                    <el-tag :type="riskTagType(row.severity)" size="small">{{ riskLabel(row.severity) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="note" label="说明" show-overflow-tooltip />
              </el-table>
            </el-card>
          </div>
        </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElUpload } from 'element-plus/es/components/upload/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/upload/style/css'
import { LocationInformation, Upload } from '@element-plus/icons-vue'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import { useContractReviews } from '../../composables/useContractReviews'
import { useContractRiskPresentation, clauseLabel, formatDate, riskTagType, riskLabel, statusLabel, statusTagType, sourceStatusType, sourceStatusLabel } from '../../composables/useLegalWorkspacePresentation'
import { useContractComparison } from '../../composables/useContractComparison'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'

const props = defineProps({
  caseId: { type: Number, default: null },
  downloadText: { type: Function, default: null },
})

const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const { quotaSummary, quotaHint, loadQuota } = useQuota()
const { openSourceDetail, verificationTagType } = useLegalSourceDetail()

const {
  contractForm,
  contractLoading,
  contractResult,
  contractReviews,
  uploadLoading,
  contractVersionMap,
  resubmitDraftForm,
  resubmitLoading,
  loadContractReviews,
  submitContractReview: runContractReview,
  onExpandContractReview,
  submitContractResubmit,
  handleContractUpload: uploadContractReview,
} = useContractReviews({ client: legalWorkspace, message: ElMessage, caseId: computed(() => props.caseId) })

const {
  reviewFilter,
  riskFilter,
  highlightedParagraph,
  contractContentRef,
  contractParagraphs,
  availableClauseTypes,
  filteredRisks,
  filteredContractReviews,
  resetRiskFilter,
  jumpToRisk,
} = useContractRiskPresentation({ contractForm, contractResult, contractReviews })

const submitContractReview = () => {
  runContractReview(resetRiskFilter)
  loadQuota()
}
const handleContractUpload = (file) => uploadContractReview(file, resetRiskFilter)

const { compareForm, compareLoading, compareResult, submitCompare } = useContractComparison({
  client: legalWorkspace, message: ElMessage,
})

const exportCompare = () => {
  if (!compareResult.value) return
  const r = compareResult.value
  const lines = ['# 合同冲突核对报告', '', `**合同A：** ${compareForm.value.title_a || '合同A'}`, `**合同B：** ${compareForm.value.title_b || '合同B'}`, `**差异项：** ${r.conflict_count} 项`, '', '## 核对总结', r.summary || '', '', '## 字段对比明细']
  if (r.fields?.length) {
    r.fields.forEach((item) => {
      const flag = item.conflict ? '⚠️ 不一致' : '✓ 一致'
      lines.push(`### ${item.label}（${flag}，${riskLabel(item.severity)}）`)
      lines.push(`- 合同A：${item.value_a || '未提及'}`)
      lines.push(`- 合同B：${item.value_b || '未提及'}`)
      lines.push(`- 说明：${item.note || '无'}`)
      lines.push('')
    })
  }
  lines.push('---', '*AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。*')
  if (props.downloadText) props.downloadText('合同冲突核对报告.md', lines.join('\n'))
}

const exportReview = () => {
  if (!contractResult.value) return
  const r = contractResult.value
  const lines = [`# 合同审查意见书`, ``, `**合同标题：** ${contractForm.value.title || '未命名'}`, `**审查状态：** ${statusLabel(r.status)}`, ``, `## 审查摘要`, r.summary || '', ``, `## 条款风险明细`]
  if (r.risks?.length) {
    r.risks.forEach((item, i) => {
      lines.push(`### ${i + 1}. ${item.label}（${riskLabel(item.risk_level)}）`)
      lines.push(`- 风险说明：${item.description || '无'}`)
      lines.push(`- 修改建议：${item.suggestion || '无'}`)
      lines.push('')
    })
  } else {
    lines.push('未识别到条款风险。')
  }
  lines.push('', '---', '*AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。*')
  if (props.downloadText) props.downloadText(`${contractForm.value.title || '合同审查意见书'}.md`, lines.join('\n'))
}

const submitReviewFeedback = async (score, note) => {
  if (!contractResult.value?.id) return
  try {
    await legalWorkspace.submitReviewFeedback(contractResult.value.id, { score, note })
    contractResult.value.feedback_score = score
    ElMessage.success('反馈已提交，感谢您的评价')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
  }
}

onMounted(() => {
  loadContractReviews()
})

defineExpose({
  prefill(title, content) {
    contractForm.value.title = title || ''
    contractForm.value.content = content || ''
  },
})
</script>

<style scoped>
.tab-panel { display: grid; gap: 20px; }
.card-title { font-weight: 700; font-size: 15px; }
.result-card { margin-top: 4px; }
.result-header { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.history-card { margin-top: 4px; }
.muted { color: var(--color-text-muted); font-size: 13px; }
.summary-text { color: var(--color-text-secondary); font-size: 14px; line-height: 1.6; margin: 0 0 12px; }
.ref-item { margin-bottom: 6px; }
.reference-list { margin-bottom: 8px; }
.ref-title { font-weight: 600; margin-right: 8px; }
.ref-citation { color: var(--color-text-muted); font-size: 12px; }
.ref-version { margin-left: 8px; color: var(--color-text-muted); font-size: 12px; }
.verification-tag { margin-left: 8px; }
.upload-row { display: flex; align-items: center; gap: 12px; }
.filter-row { display: flex; gap: 12px; margin-bottom: 8px; }
.contract-content { max-height: 400px; overflow-y: auto; background: var(--el-fill-color-light); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
.contract-paragraph { margin: 0 0 12px; padding: 8px 12px; font-size: 14px; line-height: 1.8; font-family: inherit; white-space: pre-wrap; word-break: break-all; border-left: 3px solid transparent; transition: all 0.3s ease; border-radius: 4px; }
.contract-paragraph.highlighted { background: var(--el-color-warning-light-9); border-left-color: var(--el-color-warning); }
.version-panel { padding: 16px; background: var(--el-fill-color-light); display: grid; gap: 16px; }
.resubmit-form { padding: 12px; background: var(--el-color-warning-light-9); border-radius: 6px; }
.version-list { margin-top: 8px; display: grid; gap: 10px; }
.version-entry { background: #fff; border-radius: 6px; padding: 10px 12px; font-size: 13px; }
.version-time, .version-status { margin-left: 8px; color: var(--color-text-muted); }
.version-content { white-space: pre-wrap; word-break: break-all; font-size: 12px; line-height: 1.6; background: var(--el-fill-color-light); padding: 8px; border-radius: 4px; margin: 6px 0 0; }
.compare-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.compare-col { min-width: 0; }
</style>
