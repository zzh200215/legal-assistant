<template>
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">法律咨询辅助</span></template>
            <el-form @submit.prevent="submitConsultation">
              <el-form-item label="描述您的法律问题">
                <el-input v-model="consultForm.question" type="textarea" :rows="4" placeholder="例如：我在公司工作了3年，公司突然辞退我，没有支付经济补偿金..." maxlength="12000" show-word-limit />
              </el-form-item>
              <el-button type="primary" :loading="consultLoading" @click="submitConsultation">提交咨询</el-button>
            </el-form>
          </el-card>

          <div v-if="consultResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">咨询结果</span>
                  <el-tag v-if="quotaHint('consultation')" size="small" effect="plain" :type="quotaSummary?.consultation?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('consultation') }}</el-tag>
                  <el-tag :type="riskTagType(consultResult.risk_level)" size="small">{{ riskLabel(consultResult.risk_level) }}</el-tag>
                  <el-tag v-if="consultResult.confidence !== undefined" :type="confidenceTagType(consultResult.confidence)" size="small" effect="plain">置信度 {{ consultResult.confidence }}%</el-tag>
                  <el-button v-if="consultResult" size="small" type="success" plain @click="emit('go-to-review', consultResult)" style="margin-left:auto">进入合同审查</el-button>
                  <el-button v-if="consultResult" size="small" type="primary" plain @click="emit('go-to-draft', consultResult)">生成文书</el-button>
                  <el-button v-if="consultResult.status === 'pending_review' || consultResult.status === 'needs_lawyer_review'" size="small" type="primary" @click="submitConsultForReview">提交律师审核</el-button>
                </div>
              </template>
              <el-descriptions :column="1" border>
                <el-descriptions-item label="问题分类">{{ categoryLabel(consultResult.category) }}</el-descriptions-item>
                <el-descriptions-item label="已知事实">
                  <ul v-if="consultResult.known_facts?.length"><li v-for="f in consultResult.known_facts" :key="f">{{ f }}</li></ul>
                  <span v-else class="muted">暂无</span>
                </el-descriptions-item>
                <el-descriptions-item label="待补充事实">
                  <ul v-if="consultResult.missing_facts?.length"><li v-for="f in consultResult.missing_facts" :key="f" class="missing-item">{{ f }}</li></ul>
                  <el-tag v-else type="success" size="small">无缺失</el-tag>
                </el-descriptions-item>
                <el-descriptions-item label="参考依据">
                  <div v-for="r in consultResult.references" :key="r.source_id" class="ref-item">
                    <el-button size="small" link type="primary" @click="openSourceDetail(r)">
                      <span class="ref-title">{{ r.title }}</span>
                    </el-button>
                    <span class="ref-citation">{{ r.citation }}</span>
                    <el-tag v-if="r.status" :type="sourceStatusType(r.status)" size="small" effect="plain">{{ sourceStatusLabel(r.status) }}</el-tag>
                    <el-tag v-if="r.verification" :type="verificationTagType(r.verification)" size="small" effect="plain" class="verification-tag">{{ r.verification.verification_note }}</el-tag>
                    <span v-if="r.version" class="ref-version">版本 {{ r.version }}</span>
                  </div>
                  <span v-if="!consultResult.references?.length" class="muted">暂无</span>
                </el-descriptions-item>
                <el-descriptions-item label="一般建议">{{ consultResult.advice }}</el-descriptions-item>
              </el-descriptions>
              <div class="followup-section">
                <el-input v-model="followupQuestion" placeholder="针对此咨询追问..." :disabled="followupLoading">
                  <template #append>
                    <el-button :loading="followupLoading" @click="submitFollowup">追问</el-button>
                  </template>
                </el-input>
              </div>
              <AiOutputFeedback :target-type="'consultation'" :target-id="consultResult.id" :value="consultResult.feedback_score" @submit="submitConsultFeedback" />
            </el-card>
          </div>

          <el-card v-if="consultations.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史咨询</span></template>
            <el-table :data="consultations" stripe size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="category" label="分类" width="120">
                <template #default="{ row }">{{ categoryLabel(row.category) }}</template>
              </el-table-column>
              <el-table-column prop="question" label="问题" show-overflow-tooltip />
              <el-table-column prop="risk_level" label="风险" width="80">
                <template #default="{ row }">
                  <el-tag :type="riskTagType(row.risk_level)" size="small">{{ riskLabel(row.risk_level) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="120">
                <template #default="{ row }">
                  <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import { useLegalConsultations } from '../../composables/useLegalConsultations'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import {
  riskTagType, riskLabel, categoryLabel,
  sourceStatusType, sourceStatusLabel, statusTagType, statusLabel,
} from '../../composables/useLegalWorkspacePresentation'

const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const props = defineProps({
  caseId: { type: Number, default: null },
  onReviewSubmitted: { type: Function, default: null },
})
const emit = defineEmits(['go-to-draft', 'go-to-review'])

const { quotaSummary, quotaHint, loadQuota } = useQuota()
const { openSourceDetail, verificationTagType } = useLegalSourceDetail()

const {
  consultForm,
  consultLoading,
  consultResult,
  consultations,
  followupQuestion,
  followupLoading,
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
})

const submitConsultation = () => {
  runConsultation()
  loadQuota()
}

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
  gap: 20px;
}
.card-title {
  font-weight: 700;
  font-size: 15px;
}
.result-card {
  margin-top: 4px;
}
.result-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.history-card {
  margin-top: 4px;
}
.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}
.missing-item {
  color: var(--el-color-danger);
  font-weight: 500;
}
.ref-item {
  margin-bottom: 6px;
}
.followup-section {
  margin-top: 12px;
}
</style>
