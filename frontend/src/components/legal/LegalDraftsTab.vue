<template>
  <div class="draft-workspace">
    <div class="app-workspace-body">
      <!-- 主栏：文书正文。标题由卡片头自带（LegalWorkspace 只挂载 tab，不再重复外层大标题） -->
      <section class="app-card draft-editor-pane">
        <header class="app-card-head draft-editor-header">
          <div class="draft-editor-title">
            <h2>{{ draftResult?.title || '新建文书草稿' }}</h2>
            <span v-if="draftResult" class="draft-editor-sub">Version {{ draftResult.version || 1 }}</span>
            <span v-if="draftResult && draftCollaboration?.updated_at" class="draft-editor-sub">最近更新 {{ formatDate(draftCollaboration.updated_at) }}</span>
          </div>
          <div class="draft-editor-actions">
            <span v-if="draftResult" class="app-tag" :class="draftSaveStateTagClass">{{ draftSaveStateLabel }}</span>
            <el-input v-if="draftResult" v-model="versionNote" class="version-note-input" size="small" placeholder="版本说明（可选）" maxlength="512" />
            <el-button v-if="draftResult" size="small" :loading="versionSaveLoading || resubmitLoading[draftResult.id]" @click="saveDraftVersion">保存版本</el-button>
            <el-button v-if="draftResult" size="small" @click="exportDraft">导出</el-button>
            <el-button v-if="draftResult" size="small" type="primary" @click="submitDraftForReview">提交审核</el-button>
          </div>
        </header>

        <div class="app-card-body draft-editor-body">
          <div v-if="draftResult?.missing_fields?.length" class="missing-facts">
            <strong>待补充事实</strong>
            <span v-for="field in draftResult.missing_fields" :key="field" class="app-tag is-pending">{{ field }}</span>
          </div>
          <el-input v-if="draftResult" v-model="editableContent" class="draft-editor" type="textarea" :rows="24" placeholder="文书正文将在这里生成，也可以直接编辑。" />
          <div v-else class="draft-editor-empty">
            <span>生成草稿后，正文会出现在这里。</span>
            <small>AI 负责起草，最终内容由你编辑和确认。</small>
          </div>

          <section v-if="draftResult" class="app-card-section draft-section">
            <div class="draft-section-head">
              <h3 class="app-card-section-title">法律依据</h3>
              <span class="draft-section-meta">{{ draftReferences.length }} 条</span>
            </div>
            <EvidenceReference v-for="reference in draftReferences" :key="reference.source_id || reference.title" :reference="reference" @open="openSourceDetail" />
            <p v-if="!draftReferences.length" class="app-empty-note">暂无引用依据。</p>
          </section>

          <section v-if="draftResult && draftVersions.length" class="app-card-section draft-section draft-version-section">
            <div class="draft-section-head">
              <h3 class="app-card-section-title">版本记录</h3>
              <span class="draft-section-meta">点击版本查看差异</span>
            </div>
            <DocumentVersion :versions="draftVersions" @select="selectVersion" @set-base="setDiffBase" @set-target="setDiffTarget" />
            <div v-if="selectedVersion" class="draft-version-actions">
              <span>已选择 Version {{ selectedVersion.version || 1 }}</span>
              <el-button size="small" :loading="restoreLoading" @click="restoreSelectedVersion">恢复此版本</el-button>
            </div>
            <div v-if="diffBase || diffTarget" class="draft-compare-summary">
              <span>对比起点：{{ diffBase ? `Version ${diffBase.version}` : '未选择' }}</span>
              <span>对比终点：{{ diffTarget ? `Version ${diffTarget.version}` : '当前正文' }}</span>
            </div>
            <DocumentDiff v-if="selectedVersion || remoteDiff" :before="remoteDiff?.from?.content || selectedVersion?.content" :after="remoteDiff?.to?.content || editableContent" :before-version="remoteDiff?.from?.version || selectedVersion?.version" :after-version="remoteDiff?.to?.version" :rows="remoteDiff?.rows || null" />
          </section>

          <section v-if="draftResult" class="app-card-section draft-section draft-comments-section">
            <div class="draft-section-head">
              <h3 class="app-card-section-title">批注</h3>
              <span class="draft-section-meta">{{ openCommentCount }} 条待处理</span>
            </div>
            <div class="comment-compose">
              <el-input v-model="commentBody" type="textarea" :rows="2" maxlength="4000" placeholder="针对正文添加批注，可说明事实缺口、修改意见或审核问题。" />
              <div class="comment-compose-actions">
                <span>正文行号（可选）</span>
                <input v-model.number="commentLineStart" type="number" min="1" placeholder="起始" />
                <input v-model.number="commentLineEnd" type="number" min="1" placeholder="结束" />
                <el-button size="small" type="primary" :loading="commentLoading" @click="submitComment">添加批注</el-button>
              </div>
            </div>
            <div v-if="draftComments.length" class="comment-list">
              <article v-for="comment in draftComments" :key="comment.id" class="comment-item" :class="{ 'is-resolved': comment.status === 'resolved' }">
                <div class="comment-meta"><strong>{{ comment.author_name || '协作者' }}</strong><span>Version {{ comment.version || draftResult.version }}</span><span v-if="comment.line_start">第 {{ comment.line_start }}{{ comment.line_end && comment.line_end !== comment.line_start ? `-${comment.line_end}` : '' }} 行</span><time>{{ formatDate(comment.created_at) }}</time></div>
                <p>{{ comment.body }}</p>
                <button type="button" @click="toggleComment(comment)">{{ comment.status === 'resolved' ? '重新打开' : '标记已处理' }}</button>
              </article>
            </div>
            <p v-else class="app-empty-note">还没有批注。对事实、风险或修改意见留下可追踪的说明。</p>
          </section>
        </div>
      </section>

      <!-- 右栏：文书结构与事实字段（窄屏时排到正文之前） -->
      <aside class="app-rail draft-form-rail">
        <section class="app-card">
          <div class="app-card-head">
            <h2>文书结构</h2>
            <span class="app-card-head-meta">{{ currentDraftFields.length }} 个字段</span>
          </div>
          <div class="app-card-body draft-form">
            <label class="draft-field-label">文书类型</label>
            <el-select v-model="draftForm.document_type" class="draft-type-select" placeholder="选择文书类型">
              <el-option v-for="template in templates" :key="template.key" :label="template.label" :value="template.key" />
            </el-select>
            <div class="draft-field-list">
              <div v-for="field in currentDraftFields" :key="field" class="draft-field">
                <label :for="`draft-field-${field}`">{{ field }}<span v-if="isDraftFieldRequired(field)">*</span></label>
                <el-input :id="`draft-field-${field}`" v-model="draftForm.fields[field]" :placeholder="isDraftFieldRequired(field) ? `请输入${field}` : '可选'" />
                <small v-if="isDraftFieldRequired(field) && !draftForm.fields[field]">生成后会标记为待补充</small>
              </div>
              <p v-if="!currentDraftFields.length" class="app-empty-note">选择文书类型后填写事实字段。</p>
            </div>
            <div class="draft-submit">
              <el-button class="generate-button" type="primary" :loading="draftLoading" @click="submitDraft">生成草稿</el-button>
              <div v-if="draftLoading" class="staged-hint" aria-live="polite">{{ draftStageText }}</div>
            </div>
          </div>
        </section>
      </aside>
    </div>

    <details class="app-card draft-history">
      <summary class="app-card-head draft-history-summary">
        <h2>文书版本</h2>
        <span class="app-card-head-meta">{{ drafts.length }} 份历史草稿<template v-if="draftHistoryHasMore">（还有更早）</template></span>
      </summary>
      <QueryStateView :status="draftsStatus" :error="draftsError" empty-text="还没有历史文书。" min-height="80px" @retry="loadDrafts">
        <div class="draft-history-list">
          <button v-for="draft in drafts" :key="draft.id" class="draft-history-row" type="button" @click="selectDraft(draft)">
            <span><strong>{{ draft.title || '未命名文书' }}</strong><small>{{ formatDate(draft.created_at) }}</small></span>
            <span>Version {{ draft.version || 1 }}</span><em>{{ statusLabel(draft.status) }}</em><em v-if="draft.submitter_name">· {{ draft.submitter_name }}</em>
          </button>
        </div>
        <div v-if="draftHistoryHasMore" class="history-load-more">
          <el-button text size="small" :loading="draftHistoryLoadingMore" @click="loadMoreDrafts">加载更多历史文书</el-button>
        </div>
      </QueryStateView>
    </details>

    <AiOutputFeedback v-if="draftResult" :target-type="'draft'" :target-id="draftResult.id" :value="draftResult.feedback_score" @submit="submitDraftFeedback" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/option/style/css'
import 'element-plus/es/components/button/style/css'
import legalWorkspace from '../../api/legalWorkspace'
import AiOutputFeedback from '../AiOutputFeedback.vue'
import EvidenceReference from './EvidenceReference.vue'
import DocumentVersion from './DocumentVersion.vue'
import DocumentDiff from './DocumentDiff.vue'
import { useLegalDrafts } from '../../composables/useLegalDrafts'
import { useQuota } from '../../composables/useQuota'
import { useLegalSourceDetail } from '../../composables/useLegalSourceDetail'
import { useStagedProgress } from '../../composables/useStagedProgress'
import { asReferenceList } from '../../utils/references'
import QueryStateView from '../QueryStateView.vue'
import { statusLabel } from '../../composables/useLegalWorkspacePresentation'

const props = defineProps({ caseId: { type: Number, default: null }, downloadText: { type: Function, default: null } })
const route = useRoute()
const router = useRouter()
const DRAFT_REQUIRED_KEYWORDS = ['申请人', '被申请人', '原告', '被告', '姓名', '身份', '金额', '日期', '地址', '请求', '证据', '投诉人', '被投诉']
const isDraftFieldRequired = (field) => DRAFT_REQUIRED_KEYWORDS.some((keyword) => field.includes(keyword))
const { loadQuota } = useQuota()
const { openSourceDetail } = useLegalSourceDetail()
const {
  templates, draftForm, draftLoading, draftResult, drafts, draftsQuery, draftFieldMap, draftVersionMap,
  draftHistoryHasMore, draftHistoryLoadingMore, loadMoreDrafts,
  loadTemplates, setTemplateFields, loadDrafts, submitDraft: runDraftSubmit,
  resubmitDraftForm, resubmitLoading, loadDraftVersions, resubmitDraft: saveDraft,
  autosaveDraft, saveDraftVersion: persistDraftVersion, restoreDraftVersion: restoreVersion,
  draftComments, draftCollaboration, loadDraftComments, loadDraftCollaboration, loadDraftDiff,
  addDraftComment, updateDraftComment,
} = useLegalDrafts({ client: legalWorkspace, message: ElMessage, caseId: computed(() => props.caseId) })

// LLM 起草约需 20-30 秒，阶段性提示缓解等待焦虑（ux-audit M-6）
const draftStageText = useStagedProgress(draftLoading, [
  '正在梳理案件事实…',
  '正在匹配文书模板与法条…',
  '正在起草文书正文…',
])
const currentDraftFields = computed(() => draftFieldMap.value[draftForm.value.document_type] || [])
// 历史草稿三态（ux-audit P1-3/M-13）：加载失败与"还没有历史文书"分离，失败可重试
const draftsStatus = computed(() => {
  if (draftsQuery.isLoading.value) return 'loading'
  if (draftsQuery.isError.value) return 'error'
  if (!drafts.value.length) return 'empty'
  return 'success'
})
const draftsError = computed(() => (draftsQuery.isError.value ? draftsQuery.error.value : null))
const editableContent = ref('')
const selectedVersion = ref(null)
const draftSaveState = ref('saved')
const restoreLoading = ref(false)
const versionSaveLoading = ref(false)
const versionNote = ref('')
const diffBase = ref(null)
const diffTarget = ref(null)
const remoteDiff = ref(null)
const commentBody = ref('')
const commentLineStart = ref(null)
const commentLineEnd = ref(null)
const commentLoading = ref(false)
const lastSavedContent = ref('')
const lastSavedFields = ref({})
let autosaveTimer = null
const draftVersions = computed(() => (draftResult.value?.id ? draftVersionMap.value[draftResult.value.id] || [] : []))
// 模型产出的 references 可能混入 null/空条目，渲染前统一过滤（否则整块卡片报错）
const draftReferences = computed(() => asReferenceList(draftResult.value?.references))
const draftSaveStateLabel = computed(() => ({
  saved: '已保存', dirty: '待保存', saving: '保存中', conflict: '存在冲突', error: '保存失败',
}[draftSaveState.value] || ''))
// 方向 B：保存状态并入共享 .app-tag 变体（成功/进行中/异常三档）
const draftSaveStateTagClass = computed(() => ({
  saved: 'is-ok', dirty: 'is-pending', saving: 'is-pending', conflict: 'is-danger', error: 'is-danger',
}[draftSaveState.value] || 'is-pending'))
const openCommentCount = computed(() => draftComments.value.filter((item) => item.status !== 'resolved').length)
const fieldsSnapshot = () => JSON.stringify(draftForm.value.fields || {})
const markSaved = (row) => {
  lastSavedContent.value = row?.content || ''
  lastSavedFields.value = JSON.parse(JSON.stringify(draftForm.value.fields || {}))
  draftSaveState.value = 'saved'
}
watch(() => draftResult.value?.content, (content) => {
  editableContent.value = content || ''
  lastSavedContent.value = content || ''
}, { immediate: true })
const runAutosave = async () => {
  if (!draftResult.value?.id || editableContent.value === lastSavedContent.value && fieldsSnapshot() === JSON.stringify(lastSavedFields.value)) return
  draftSaveState.value = 'saving'
  try {
    const row = await autosaveDraft({
      id: draftResult.value.id,
      document_type: draftResult.value.document_type || draftForm.value.document_type,
      fields: draftForm.value.fields,
      content: editableContent.value,
      base_row_version: draftResult.value.row_version,
    })
    markSaved(row)
    await loadDraftCollaboration(row.id)
  } catch (error) {
    if (error.response?.status === 409) {
      draftSaveState.value = 'conflict'
      ElMessage.warning(error.response?.data?.error?.detail || '文书已被其他更新覆盖，请刷新后确认差异')
    } else {
      draftSaveState.value = 'error'
    }
  }
}
const scheduleAutosave = () => {
  if (!draftResult.value?.id) return
  if (editableContent.value === lastSavedContent.value && fieldsSnapshot() === JSON.stringify(lastSavedFields.value)) return
  draftSaveState.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(runAutosave, 1000)
}
watch(editableContent, scheduleAutosave)
watch(() => draftForm.value.fields, scheduleAutosave, { deep: true })
// 把「当前正在编辑的草稿」写进 URL：右栏助手读 URL 就知道用户在看哪一份，
// 刷新或直达也能回到同一份（与咨询 tab 的 consultation_id 同一套做法）。
watch(() => draftResult.value?.id, (id) => {
  const current = Number(route.query.draft_id) || null
  if ((id ? Number(id) : null) === current) return
  router.replace({ query: { ...route.query, draft_id: id ? String(id) : undefined } })
})
const submitDraft = async () => {
  await runDraftSubmit()
  if (draftResult.value) {
    markSaved(draftResult.value)
    await Promise.all([loadDraftComments(draftResult.value.id), loadDraftCollaboration(draftResult.value.id)])
    diffBase.value = null
    diffTarget.value = null
    remoteDiff.value = null
  }
  await loadQuota()
}
const saveDraftVersion = async () => {
  if (!draftResult.value) return
  versionSaveLoading.value = true
  try {
    let row
    if (['needs_facts', 'returned_for_facts'].includes(draftResult.value.status)) {
      resubmitDraftForm.value = { ...resubmitDraftForm.value, [draftResult.value.id]: editableContent.value }
      row = await saveDraft({
        id: draftResult.value.id,
        document_type: draftResult.value.document_type || draftForm.value.document_type,
        fields: draftForm.value.fields,
        content: editableContent.value,
      })
    } else {
      row = await persistDraftVersion({
        id: draftResult.value.id,
        document_type: draftResult.value.document_type || draftForm.value.document_type,
        fields: draftForm.value.fields,
        content: editableContent.value,
        base_row_version: draftResult.value.row_version,
        version_note: versionNote.value,
      })
    }
    if (row) markSaved(row)
    versionNote.value = ''
  } catch (error) {
    if (error.response?.status === 409) {
      draftSaveState.value = 'conflict'
      ElMessage.warning(error.response?.data?.error?.detail || '文书已被其他更新覆盖，请刷新后确认差异')
    }
  } finally {
    versionSaveLoading.value = false
  }
  selectedVersion.value = null
  await loadDraftVersions(draftResult.value.id)
}
const submitDraftForReview = async () => {
  if (!draftResult.value?.id) return
  try {
    await legalWorkspace.submitLegalReviewAction('draft', draftResult.value.id, { action: 'submit_review', note: '用户提交律师审核' })
    draftResult.value.status = 'needs_lawyer_review'
    ElMessage.success('已提交律师审核')
  } catch (error) { ElMessage.error(error.response?.data?.detail || '提交审核失败') }
}
const selectDraft = async (draft) => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  draftSaveState.value = 'saved'
  draftResult.value = { ...draft, content: draft.content || '' }
  draftForm.value.document_type = draft.document_type || draftForm.value.document_type
  draftForm.value.fields = { ...(draft.fields || {}) }
  editableContent.value = draft.content || ''
  lastSavedContent.value = editableContent.value
  lastSavedFields.value = JSON.parse(JSON.stringify(draftForm.value.fields || {}))
  await loadDraftVersions(draft.id)
  await Promise.all([loadDraftComments(draft.id), loadDraftCollaboration(draft.id)])
  diffBase.value = null
  diffTarget.value = null
  remoteDiff.value = null
  selectedVersion.value = null
}
const selectVersion = (version) => { selectedVersion.value = version }
const refreshDiff = async () => {
  if (!draftResult.value?.id || !diffBase.value || !diffTarget.value) {
    remoteDiff.value = null
    return
  }
  try {
    remoteDiff.value = await loadDraftDiff(draftResult.value.id, diffBase.value.id, diffTarget.value.id)
  } catch {
    remoteDiff.value = null
  }
}
const setDiffBase = (version) => { diffBase.value = version; refreshDiff() }
const setDiffTarget = (version) => { diffTarget.value = version; refreshDiff() }
const submitComment = async () => {
  if (!draftResult.value?.id || !commentBody.value.trim()) {
    ElMessage.warning('请输入批注内容')
    return
  }
  commentLoading.value = true
  try {
    await addDraftComment({
      id: draftResult.value.id,
      body: commentBody.value,
      version: draftResult.value.version,
      line_start: commentLineStart.value || undefined,
      line_end: commentLineEnd.value || undefined,
    })
    commentBody.value = ''
    commentLineStart.value = null
    commentLineEnd.value = null
    ElMessage.success('批注已添加')
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || '添加批注失败')
  } finally {
    commentLoading.value = false
  }
}
const toggleComment = async (comment) => {
  try {
    await updateDraftComment({ id: draftResult.value.id, commentId: comment.id, status: comment.status === 'resolved' ? 'open' : 'resolved' })
  } catch (error) {
    ElMessage.error(error.response?.data?.error?.detail || '更新批注失败')
  }
}
const restoreSelectedVersion = async () => {
  if (!draftResult.value?.id || !selectedVersion.value?.id) return
  restoreLoading.value = true
  try {
    const row = await restoreVersion({
      id: draftResult.value.id,
      versionId: selectedVersion.value.id,
      base_row_version: draftResult.value.row_version,
    })
    draftForm.value.document_type = row.document_type || draftForm.value.document_type
    draftForm.value.fields = { ...(row.fields || draftForm.value.fields) }
    editableContent.value = row.content || ''
    markSaved(row)
    selectedVersion.value = null
    await loadDraftVersions(draftResult.value.id)
    await loadDraftCollaboration(draftResult.value.id)
  } catch (error) {
    if (error.response?.status === 409) {
      draftSaveState.value = 'conflict'
      ElMessage.warning(error.response?.data?.error?.detail || '文书已被其他更新覆盖，请刷新后确认差异')
    } else {
      ElMessage.error(error.response?.data?.error?.detail || '恢复版本失败')
    }
  } finally {
    restoreLoading.value = false
  }
}
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'
const exportDraft = async () => {
  if (!draftResult.value) return
  try {
    const { data } = await legalWorkspace.exportLegalDraftDocx(draftResult.value.id)
    const url = URL.createObjectURL(data); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${draftResult.value.title || '法律文书草稿'}.docx`; anchor.click(); URL.revokeObjectURL(url)
  } catch {
    if (props.downloadText) props.downloadText(`${draftResult.value.title || '法律文书草稿'}.md`, `# ${draftResult.value.title || '法律文书草稿'}\n\n${editableContent.value}`)
  }
}
const submitDraftFeedback = async (score, note) => {
  if (!draftResult.value?.id) return
  try { await legalWorkspace.submitDraftFeedback(draftResult.value.id, { score, note }); draftResult.value.feedback_score = score; ElMessage.success('反馈已提交') } catch (error) { ElMessage.error(error.response?.data?.detail || '反馈提交失败') }
}
const openLatestDraft = async () => {
  await loadDrafts()
  if (draftResult.value || !drafts.value.length) return
  // 刷新或带着 draft_id 进来时，回到同一份草稿（右栏助手据此把这份文书交给后端）
  const requestedId = Number(route.query.draft_id) || null
  const requested = requestedId ? drafts.value.find((row) => Number(row.id) === requestedId) : null
  await selectDraft(requested || drafts.value[0])
}
onMounted(() => {
  setTemplateFields({
    labor_arbitration_application: ['申请人', '被申请人', '劳动关系起止时间', '仲裁请求', '事实与理由', '证据清单'],
    private_lending_complaint: ['原告', '被告', '借款金额', '借款日期', '诉讼请求', '事实与理由', '证据清单'],
    consumer_complaint: ['投诉人', '被投诉企业', '购买商品或服务', '消费金额与日期', '投诉请求', '事实与理由', '证据清单'],
    supplementary_agreement: ['甲方', '乙方', '原协议名称', '补充事项', '生效日期', '签署地点'],
  }); loadTemplates(); openLatestDraft()
})
onBeforeUnmount(() => { if (autosaveTimer) clearTimeout(autosaveTimer) })
defineExpose({ prefill(documentType, fields) { if (documentType) draftForm.value.document_type = documentType; draftForm.value.fields = fields || {} } })
</script>

<style scoped>
.draft-workspace { display: grid; gap: var(--space-4); }

/* 右栏：文书结构（类型 + 事实字段 + 生成） */
.draft-form-rail { align-content: start; }
.draft-form { display: grid; gap: var(--space-2); }
.draft-field-label { color: var(--color-text-secondary); font-size: var(--text-xs); }
.draft-type-select { width: 100%; }
.draft-field-list { display: grid; gap: var(--space-3); margin-top: var(--space-1); }
.draft-field { display: grid; gap: var(--space-1); }
.draft-field label { color: var(--color-text-secondary); font-size: var(--text-sm); }
.draft-field label span { margin-left: 2px; color: var(--color-danger); }
.draft-field small { color: var(--color-warning); font-size: var(--text-xs); line-height: 1.45; }
.draft-submit { display: grid; gap: var(--space-2); margin-top: var(--space-1); }
.generate-button { width: 100%; }
.staged-hint { color: var(--color-text-muted); font-size: var(--text-xs); text-align: center; }

/* 主栏：文书正文与分段 */
.draft-editor-pane { min-width: 0; }
.draft-editor-header { flex-wrap: wrap; row-gap: var(--space-2); }
.draft-editor-title { display: grid; gap: 2px; min-width: 0; }
.draft-editor-title h2 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.draft-editor-sub { color: var(--color-text-muted); font-size: var(--text-xs); }
.draft-editor-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: var(--space-2); margin-left: auto; }
.version-note-input { width: 150px; }
.draft-editor-body { display: grid; gap: var(--space-3); }
.missing-facts { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; color: var(--color-warning); font-size: var(--text-xs); }
.draft-editor { width: 100%; }
.draft-editor :deep(textarea) { min-height: 390px; padding: 17px 19px; border-color: var(--color-border-light); color: var(--color-text); font-family: var(--font-family); font-size: var(--text-base); line-height: 1.9; resize: vertical; }
.draft-editor-empty { display: grid; place-items: center; align-content: center; gap: var(--space-2); min-height: 440px; color: var(--color-text-muted); font-size: var(--text-sm); text-align: center; }
.draft-editor-empty small { font-size: var(--text-xs); }

.draft-section { padding-top: var(--space-4); border-top: 1px solid var(--color-border-light); }
.draft-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); }
.draft-section-head .app-card-section-title { margin: 0; }
.draft-section-meta { color: var(--color-text-muted); font-size: var(--text-xs); }
.draft-version-section { display: grid; gap: var(--space-3); }
.draft-version-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: var(--space-3); color: var(--color-text-muted); font-size: var(--text-xs); }
.draft-compare-summary { display: flex; flex-wrap: wrap; gap: var(--space-3); color: var(--color-text-muted); font-size: var(--text-xs); }
.draft-comments-section { display: grid; gap: var(--space-3); }
.comment-compose { display: grid; gap: var(--space-2); }
.comment-compose-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; color: var(--color-text-muted); font-size: var(--text-xs); }
.comment-compose-actions input { width: 62px; padding: 5px 6px; border: 1px solid var(--color-border); border-radius: var(--radius-xs); background: var(--color-surface); color: var(--color-text); font-family: var(--font-family); font-size: var(--text-xs); }
.comment-list { display: grid; gap: var(--space-2); }
.comment-item { padding: 10px 12px; border: 1px solid var(--color-border-light); border-left: 3px solid var(--color-primary); border-radius: var(--radius-md); background: var(--color-surface-subtle); }
.comment-item.is-resolved { border-left-color: var(--color-border); opacity: .72; }
.comment-meta { display: flex; flex-wrap: wrap; gap: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); }
.comment-meta strong { color: var(--color-text); font-weight: 600; }
.comment-meta time { margin-left: auto; }
.comment-item p { margin: 6px 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.6; white-space: pre-wrap; }
.comment-item > button { padding: 0; border: 0; background: transparent; color: var(--color-primary); font-family: var(--font-family); font-size: var(--text-xs); cursor: pointer; }

/* 历史草稿：可折叠卡片（summary 复用卡片头版式） */
.draft-history { overflow: hidden; }
.draft-history-summary { border-bottom-color: transparent; cursor: pointer; user-select: none; }
.draft-history-summary::after { content: '展开'; margin-left: var(--space-2); color: var(--color-text-muted); font-size: var(--text-xs); font-weight: 500; }
.draft-history[open] .draft-history-summary { border-bottom-color: var(--color-border-light); }
.draft-history[open] .draft-history-summary::after { content: '收起'; }
.draft-history-list { display: grid; }
.draft-history-row { display: grid; grid-template-columns: minmax(0, 1fr) 90px 90px; align-items: center; gap: var(--space-3); min-height: 48px; padding: var(--space-2) 0; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text-secondary); font-family: var(--font-family); text-align: left; cursor: pointer; }
.draft-history-row:hover { background: var(--color-surface-hover); }
.draft-history-row > span:first-child { display: grid; gap: 3px; }
.draft-history-row strong { color: var(--color-text); font-size: var(--text-sm); font-weight: 600; }
.draft-history-row small, .draft-history-row span:nth-child(2), .draft-history-row em { color: var(--color-text-muted); font-size: var(--text-xs); font-style: normal; }

@media (max-width: 1100px) {
  .draft-form-rail { order: -1; }
}
@media (max-width: 540px) {
  .draft-editor-actions { justify-content: flex-start; margin-left: 0; }
  .draft-history-row { grid-template-columns: minmax(0, 1fr) auto; }
  .draft-history-row em { grid-column: 2; }
  .version-note-input { width: 100%; }
  .comment-meta time { margin-left: 0; }
}
.history-load-more { display: flex; justify-content: center; margin-top: var(--space-2); }
</style>
