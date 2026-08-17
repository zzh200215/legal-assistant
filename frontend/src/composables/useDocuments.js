import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../api'
import { buildAgentDemoRouteQuery } from '../utils/agentDemo'
import { useDocumentList } from './useDocumentList'
import { useDocumentDetail } from './useDocumentDetail'
import { useDocumentAnalysis } from './useDocumentAnalysis'
import { useDocumentQa } from './useDocumentQa'
import { useDocumentCompare } from './useDocumentCompare'
import { errorMessage } from '../api/errors'

// 文档知识库视图（Documents.vue）共享状态门面：
// 按领域拆分为 列表/上传、详情、分析长任务、问答、对比 五个模块（useDocument*），
// 本文件仅做编排（路由参数、跨领域复位、导航）并保持与旧版完全一致的导出面，
// 子组件（DocumentSidebar / DocumentWorkspace / 壳视图）无需改动。
// 模块级单例：所有消费者共享同一份状态，避免双实例分叉。

const retrievalForm = ref({
  topK: '8',
  rerankTopN: '5',
  rewriteMode: 'auto',
  contextExpand: '1',
})

let instance = null

export function useDocuments() {
  if (!instance) instance = createDocuments()
  return instance
}

function createDocuments() {
  const route = useRoute()
  const router = useRouter()

  const list = useDocumentList()
  const detail = useDocumentDetail({ documents: list.documents })
  const analysis = useDocumentAnalysis({
    docId: detail.docId,
    parseJobs: detail.parseJobs,
    onCompleted: () => {
      list.fetchDocuments()
      detail.fetchParseJobs()
    },
  })
  const qa = useDocumentQa({ docId: detail.docId, onAsked: () => detail.fetchQaRecords() })
  const compare = useDocumentCompare({ docId: detail.docId })

  const replaceDocumentQuery = (documentId) => {
    const nextQuery = { ...route.query }
    if (documentId) {
      nextQuery.documentId = String(documentId)
    } else {
      delete nextQuery.documentId
    }
    router.replace({ query: nextQuery })
  }

  const openTask = (taskId) => {
    router.push({ path: '/tasks', query: { taskId: String(taskId), view: 'table' } })
  }

  const openAgentRun = (runId) => {
    router.push({ path: '/agent', query: { runId: String(runId) } })
  }

  const openDocumentTasks = () => {
    if (!detail.docId.value) return
    router.push({
      path: '/tasks',
      query: {
        view: 'table',
        scope: 'all',
        sourceType: 'document',
        sourceId: String(detail.docId.value),
      },
    })
  }

  const openAgentDemo = () => {
    if (!detail.docId.value) return
    router.push({
      path: '/agent',
      query: buildAgentDemoRouteQuery('document_risk', {
        documentId: detail.docId.value,
        documentTitle: detail.docMeta.value?.title || '',
      }),
    })
  }

  /** 选中文档：复位问答/对比/分析后加载详情并触发分析 */
  const selectDocument = async (item) => {
    compare.resetForNewDocument()
    qa.resetForNewDocument()
    analysis.reset()
    replaceDocumentQuery(item.id)
    await detail.selectDocument(item)
    await analysis.runAnalysis()
  }

  /** 上传 + 分析：一次动作集成上传、清空、刷新与触发分析（幂等键防连点重复入库） */
  const uploadAndAnalyze = async () => {
    await list.uploadAndAnalyze(async (firstDocument) => {
      compare.resetForNewDocument()
      qa.resetForNewDocument()
      analysis.reset()
      await detail.setUploadedDocument(firstDocument)
      await Promise.all([list.fetchDocuments(), list.fetchKnowledgeBases()])
      await analysis.runAnalysis()
    })
  }

  /** 路由直达文档加载（未在当前列表页时拉详情并插入列表缓存） */
  const loadDocumentFromRoute = async (rawDocumentId) => {
    const nextId = Number(rawDocumentId)
    if (!Number.isFinite(nextId) || nextId <= 0) return
    const existing = list.documents.value.find((item) => item.id === nextId)
    if (existing) {
      if (detail.docId.value !== existing.id) {
        await selectDocument(existing)
      }
      return
    }
    try {
      const { data } = await api.getDocument(nextId)
      const normalized = {
        id: data.id,
        title: data.title,
        file_type: data.file_type,
        knowledge_base_id: data.knowledge_base_id,
        version_number: data.version_number,
        classification: data.classification,
        sensitivity_level: data.sensitivity_level,
        permission_scope: data.permission_scope,
        status: data.status,
        summary: data.summary,
        created_at: data.created_at,
        metadata_json: data.metadata_json || null,
      }
      list.prependDocument(normalized)
      await selectDocument(normalized)
    } catch (error) {
      ElMessage.error(errorMessage(error) || '文档加载失败')
    }
  }

  const initialize = async (rawDocumentId) => {
    analysis.reset()
    await list.fetchKnowledgeBases()
    await list.fetchDocuments()
    await loadDocumentFromRoute(rawDocumentId)
  }

  return {
    // list
    file: list.file,
    selectedFiles: list.selectedFiles,
    uploading: list.uploading,
    uploadForm: list.uploadForm,
    filters: list.filters,
    documentPage: list.documentPage,
    documentPageSize: list.documentPageSize,
    documents: list.documents,
    documentTotal: list.documentTotal,
    knowledgeBases: list.knowledgeBases,
    classificationOptions: list.classificationOptions,
    fetchDocuments: list.fetchDocuments,
    fetchKnowledgeBases: list.fetchKnowledgeBases,
    handleDocumentPageChange: list.handleDocumentPageChange,
    handleFilterChange: list.handleFilterChange,
    onFileChange: list.onFileChange,
    uploadAndAnalyze,
    // detail
    docId: detail.docId,
    docMeta: detail.docMeta,
    versions: detail.versions,
    parseJobs: detail.parseJobs,
    qaRecords: detail.qaRecords,
    relatedAgentRuns: detail.relatedAgentRuns,
    downloading: detail.downloading,
    downloadPolicySaving: detail.downloadPolicySaving,
    selectDocument,
    loadDocumentFromRoute,
    fetchParseJobs: detail.fetchParseJobs,
    fetchVersions: detail.fetchVersions,
    fetchQaRecords: detail.fetchQaRecords,
    fetchRelatedAgentRuns: detail.fetchRelatedAgentRuns,
    downloadCurrentDocument: detail.downloadCurrentDocument,
    updateDownloadPolicy: detail.updateDownloadPolicy,
    // analysis
    analysis: analysis.analysis,
    loading: analysis.loading,
    analysisTask: analysis.analysisTask,
    analysisTaskMessage: analysis.analysisTaskMessage,
    runAnalysis: analysis.runAnalysis,
    retryParse: analysis.retryParse,
    clearAnalysisPolling: analysis.clearAnalysisPolling,
    // qa
    qaQuestion: qa.qaQuestion,
    qaResult: qa.qaResult,
    asking: qa.asking,
    submittingFeedback: qa.submittingFeedback,
    negativeFeedbackVisible: qa.negativeFeedbackVisible,
    feedbackForm: qa.feedbackForm,
    feedbackReasonOptions: qa.feedbackReasonOptions,
    feedbackValueText: qa.feedbackValueText,
    feedbackTagType: qa.feedbackTagType,
    askDocumentQuestion: qa.askDocumentQuestion,
    submitPositiveFeedback: qa.submitPositiveFeedback,
    openNegativeFeedback: qa.openNegativeFeedback,
    cancelNegativeFeedback: qa.cancelNegativeFeedback,
    submitNegativeFeedback: qa.submitNegativeFeedback,
    // compare
    compareSelection: compare.compareSelection,
    compareResult: compare.compareResult,
    compareLoading: compare.compareLoading,
    conflictCases: compare.conflictCases,
    conflictSuggestionLoading: compare.conflictSuggestionLoading,
    confirmedConflictCount: compare.confirmedConflictCount,
    createdTasks: compare.createdTasks,
    creatingTasks: compare.creatingTasks,
    runCompare: compare.runCompare,
    createConflictSuggestions: compare.createConflictSuggestions,
    confirmConflictTask: compare.confirmConflictTask,
    createTasks: compare.createTasks,
    // navigation
    openAgentDemo,
    openAgentRun,
    openTask,
    openDocumentTasks,
    // misc
    retrievalForm,
    initialize,
  }
}
