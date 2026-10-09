<template>
  <section class="case-documents">
    <div class="case-documents-toolbar">
      <div>
        <h3>案件材料</h3>
        <p>合同、证据和案件相关资料统一归档在当前案件下。</p>
      </div>
      <button type="button" class="case-documents-link" @click="router.push({ path: '/documents', query: { case_id: String(caseId) } })">打开完整资料库</button>
    </div>

    <div class="case-document-filters">
      <button v-for="item in categories" :key="item.value" type="button" :class="{ active: category === item.value }" @click="category = item.value">{{ item.label }}</button>
    </div>

    <div v-if="loading" class="case-documents-empty">正在加载案件材料…</div>
    <div v-else-if="error" class="case-documents-error">{{ error }}</div>
    <div v-else-if="!filteredDocuments.length" class="case-documents-empty">当前案件暂无{{ category ? categoryLabel(category) : '关联' }}材料。</div>
    <div v-else class="case-document-list">
      <button v-for="item in filteredDocuments" :key="item.id" type="button" class="case-document-row" @click="openDocument(item)">
        <span class="case-document-kind">{{ documentKindLabel(item) }}</span>
        <span class="case-document-main"><strong>{{ item.title }}</strong><small>{{ item.file_type }} · v{{ item.version_number || 1 }} · {{ item.status || '处理中' }}</small></span>
        <span class="case-document-meta">{{ formatDate(item.created_at) }}</span>
      </button>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'

const props = defineProps({ caseId: { type: [Number, String], required: true } })
const router = useRouter()
const documents = ref([])
const loading = ref(false)
const error = ref('')
const category = ref('')
const categories = [
  { value: '', label: '全部' },
  { value: 'contract', label: '合同' },
  { value: 'evidence', label: '证据' },
  { value: 'statute', label: '法规' },
  { value: 'case', label: '案例' },
]
const categoryMap = { contract: ['contract', 'contract_template'], evidence: ['evidence', 'case_material'], statute: ['statute', 'regulation', 'judicial_interpretation'], case: ['case', 'case_summary'] }
const categoryLabel = (value) => categories.find((item) => item.value === value)?.label || '资料'
const documentKind = (item) => item.document_kind || item.classification || ''
const filteredDocuments = computed(() => {
  if (!category.value) return documents.value
  const accepted = categoryMap[category.value] || []
  return documents.value.filter((item) => accepted.includes(documentKind(item)))
})
const documentKindLabel = (item) => {
  const kind = documentKind(item)
  if (categoryMap.contract.includes(kind)) return '合同'
  if (categoryMap.evidence.includes(kind)) return '证据'
  if (categoryMap.statute.includes(kind)) return '法规'
  if (categoryMap.case.includes(kind)) return '案例'
  return kind || '材料'
}
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

const load = async () => {
  const caseId = Number(props.caseId)
  if (!Number.isFinite(caseId) || caseId <= 0) return
  loading.value = true
  error.value = ''
  try {
    const { data } = await api.listDocuments({ case_id: caseId, page: 1, page_size: 100 })
    documents.value = data?.items || []
  } catch (err) {
    error.value = err.response?.data?.detail || '案件材料暂时无法加载'
  } finally {
    loading.value = false
  }
}
const openDocument = (item) => router.push({ path: '/documents', query: { documentId: String(item.id), case_id: String(props.caseId) } })
watch(() => props.caseId, load, { immediate: true })
</script>

<style scoped>
.case-documents { display: grid; gap: 14px; }
.case-documents-toolbar { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; padding-bottom: 12px; border-bottom: 1px solid var(--color-border); }
.case-documents-toolbar h3 { margin: 0; color: var(--color-text); font-size: 15px; font-weight: 600; }.case-documents-toolbar p { margin: 4px 0 0; color: var(--color-text-muted); font-size: 12px; }.case-documents-link { padding: 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.case-document-filters { display: flex; gap: 16px; border-bottom: 1px solid var(--color-border-light); }.case-document-filters button { padding: 0 0 7px; border: 0; border-bottom: 2px solid transparent; background: transparent; color: var(--color-text-muted); font: inherit; font-size: 12px; cursor: pointer; }.case-document-filters button.active { border-color: var(--color-primary); color: var(--color-primary); font-weight: 600; }
.case-document-list { display: grid; }.case-document-row { display: grid; grid-template-columns: 58px minmax(0, 1fr) auto; align-items: center; gap: 12px; min-height: 54px; padding: 8px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }.case-document-row:hover { background: #F8FAFB; }.case-document-kind, .case-document-meta { color: var(--color-text-muted); font-size: 11px; }.case-document-main { display: grid; gap: 3px; min-width: 0; }.case-document-main strong { overflow: hidden; font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }.case-document-main small { color: var(--color-text-muted); font-size: 11px; }.case-documents-empty, .case-documents-error { padding: 18px 0; color: var(--color-text-muted); font-size: 12px; }.case-documents-error { color: var(--color-danger); }
@media (max-width: 640px) { .case-documents-toolbar { align-items: flex-start; flex-direction: column; }.case-document-row { grid-template-columns: 52px minmax(0, 1fr); }.case-document-meta { grid-column: 2; }.case-document-filters { overflow-x: auto; } }
</style>
