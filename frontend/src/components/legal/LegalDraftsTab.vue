<template>
        <div class="tab-panel">
          <el-card shadow="never">
            <template #header><span class="card-title">法律文书草稿</span></template>
            <el-form @submit.prevent="submitDraft">
              <el-form-item label="文书类型">
                <el-select v-model="draftForm.document_type" placeholder="选择文书类型" style="width:100%">
                  <el-option v-for="t in templates" :key="t.key" :label="t.label" :value="t.key" />
                </el-select>
              </el-form-item>
              <el-divider content-position="left">事实字段</el-divider>
              <el-form-item v-for="field in currentDraftFields" :key="field" :label="field" :required="isDraftFieldRequired(field)">
                <el-input v-model="draftForm.fields[field]" :placeholder="isDraftFieldRequired(field) ? `【必填】请输入${field}` : `请输入${field}`" />
                <span v-if="isDraftFieldRequired(field) && !draftForm.fields[field]" class="required-hint">此项为必填，缺失将标记为【待补充】</span>
              </el-form-item>
              <el-button type="primary" :loading="draftLoading" @click="submitDraft">生成草稿</el-button>
            </el-form>
          </el-card>

          <div v-if="draftResult" class="result-card">
            <el-card shadow="never">
              <template #header>
                <div class="result-header">
                  <span class="card-title">{{ draftResult.title }}</span>
                  <el-tag v-if="quotaHint('draft')" size="small" effect="plain" :type="quotaSummary?.draft?.remaining <= 0 ? 'danger' : 'warning'">{{ quotaHint('draft') }}</el-tag>
                  <el-tag v-if="draftResult.missing_fields?.length" type="danger" size="small">缺失 {{ draftResult.missing_fields.length }} 项</el-tag>
                  <el-tag v-else type="success" size="small">字段完整</el-tag>
                  <el-tag v-if="draftResult.confidence !== undefined" :type="confidenceTagType(draftResult.confidence)" size="small" effect="plain">置信度 {{ draftResult.confidence }}%</el-tag>
                  <el-button size="small" @click="exportDraft" style="margin-left:auto">导出草稿</el-button>
                </div>
              </template>
              <div v-if="draftResult.missing_fields?.length" class="missing-warn">
                <strong>待补充事实：</strong>
                <el-tag v-for="f in draftResult.missing_fields" :key="f" type="danger" size="small" style="margin:2px 4px">{{ f }}</el-tag>
              </div>
              <el-divider />
              <pre class="draft-content">{{ draftResult.content }}</pre>
              <el-divider content-position="left">参考依据</el-divider>
              <div v-if="draftResult.references?.length" class="reference-list">
                <div v-for="r in draftResult.references" :key="r.source_id" class="ref-item">
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
              <AiOutputFeedback :target-type="'draft'" :target-id="draftResult.id" :value="draftResult.feedback_score" @submit="submitDraftFeedback" />
            </el-card>
          </div>

          <el-card v-if="drafts.length" shadow="never" class="history-card">
            <template #header><span class="card-title">历史草稿</span></template>
            <el-table :data="drafts" stripe size="small">
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="title" label="文书" show-overflow-tooltip />
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
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import { useLegalDrafts } from '../../composables/useLegalDrafts'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import { sourceStatusType, sourceStatusLabel, statusTagType, statusLabel } from '../../composables/useLegalWorkspacePresentation'

const props = defineProps({
  caseId: { type: Number, default: null },
  downloadText: { type: Function, default: null },
})

const confidenceTagType = (score) => {
  if (score >= 80) return 'success'
  if (score >= 55) return 'warning'
  return 'danger'
}

const DRAFT_REQUIRED_KEYWORDS = ['申请人', '被申请人', '原告', '被告', '姓名', '身份', '金额', '日期', '地址', '请求', '证据', '投诉人', '被投诉']
const isDraftFieldRequired = (field) => DRAFT_REQUIRED_KEYWORDS.some((kw) => field.includes(kw))

const { quotaSummary, quotaHint, loadQuota } = useQuota()
const { openSourceDetail, verificationTagType } = useLegalSourceDetail()

const {
  templates,
  draftForm,
  draftLoading,
  draftResult,
  drafts,
  draftFieldMap,
  loadTemplates,
  setTemplateFields,
  loadDrafts,
  submitDraft: runDraftSubmit,
} = useLegalDrafts({ client: legalWorkspace, message: ElMessage, caseId: computed(() => props.caseId) })

const currentDraftFields = computed(() => draftFieldMap.value[draftForm.value.document_type] || [])

const submitDraft = () => {
  runDraftSubmit()
  loadQuota()
}

const buildDraftMarkdown = (d) => {
  const lines = [`# ${d.title || '法律文书草稿'}`]
  if (d.missing_fields?.length) {
    lines.push('', `**待补充字段：** ${d.missing_fields.join('、')}`)
  }
  lines.push('', '---', d.content || '')
  return lines.join('\n')
}

const exportDraft = async () => {
  if (!draftResult.value) return
  const d = draftResult.value
  try {
    const { data } = await legalWorkspace.exportLegalDraftDocx(d.id)
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url
    a.download = `${d.title || '法律文书草稿'}.docx`
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    if (props.downloadText) props.downloadText(`${d.title || '法律文书草稿'}.md`, buildDraftMarkdown(d))
  }
}

const submitDraftFeedback = async (score, note) => {
  if (!draftResult.value?.id) return
  try {
    await legalWorkspace.submitDraftFeedback(draftResult.value.id, { score, note })
    draftResult.value.feedback_score = score
    ElMessage.success('反馈已提交，感谢您的评价')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '反馈提交失败')
  }
}

onMounted(() => {
  setTemplateFields({
    labor_arbitration_application: ['申请人', '被申请人', '劳动关系起止时间', '仲裁请求', '事实与理由', '证据清单'],
    private_lending_complaint: ['原告', '被告', '借款金额', '借款日期', '诉讼请求', '事实与理由', '证据清单'],
    consumer_complaint: ['投诉人', '被投诉企业', '购买商品或服务', '消费金额与日期', '投诉请求', '事实与理由', '证据清单'],
    supplementary_agreement: ['甲方', '乙方', '原协议名称', '补充事项', '生效日期', '签署地点'],
  })
  loadTemplates()
  loadDrafts()
})

defineExpose({
  prefill(document_type, fields) {
    if (document_type) draftForm.value.document_type = document_type
    draftForm.value.fields = fields || {}
  },
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
.reference-list {
  margin-bottom: 8px;
}
.ref-title {
  font-weight: 600;
  margin-right: 8px;
}
.ref-citation {
  color: var(--color-text-muted);
  font-size: 12px;
}
.ref-version {
  margin-left: 8px;
  color: var(--color-text-muted);
  font-size: 12px;
}
.verification-tag {
  margin-left: 8px;
}
.missing-warn {
  padding: 12px;
  background: var(--el-color-danger-light-9);
  border-radius: 6px;
  margin-bottom: 12px;
  font-size: 13px;
}
.draft-content {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
  line-height: 1.8;
  background: var(--el-fill-color-light);
  padding: 20px;
  border-radius: 8px;
  margin: 0;
  font-family: inherit;
}
.required-hint {
  display: block;
  font-size: 12px;
  color: var(--el-color-danger);
  margin-top: 2px;
}
</style>
