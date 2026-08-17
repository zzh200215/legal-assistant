import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk, qkPrefix } from '../query/keys'
import { setQueryData, getQueryData } from '../query/cache'
import api from '../api'

// 文档知识库「列表 + 上传」领域模块：列表/筛选/分页/知识库/上传。
// 数据经统一查询层（同 key 去重、缓存、取消、重试、离线暂停），
// 写操作（上传）经 useMutation 自动携带 Idempotency-Key，防止连点重复入库。

const classificationOptions = ['statute', 'judicial_interpretation', 'case_summary', 'contract_template', 'draft_template', 'regulation', 'general']

export function useDocumentList() {
  const documentPage = ref(1)
  const documentPageSize = ref(10)
  const filters = ref({
    knowledge_base_id: null,
    classification: '',
    sensitivity_level: '',
    q: '',
  })
  const uploadForm = ref({
    knowledge_base_name: '',
    classification: '',
    tags: '',
    sensitivity_level: 'internal',
    permission_scope: 'private',
    permission_users: '',
    permission_roles: '',
  })
  const selectedFiles = ref([])
  const file = ref(null)
  const uploading = ref(false)

  const knowledgeBasesQuery = useQuery({
    key: qk.documents.knowledgeBases(),
    fetcher: () => api.listKnowledgeBases(),
    staleTime: 60 * 1000,
  })

  const documentsQuery = useQuery({
    key: () => qk.documents.list({
      page: documentPage.value,
      pageSize: documentPageSize.value,
      filters: filters.value,
    }),
    fetcher: ({ signal }) => api.listDocuments({
      page: documentPage.value,
      page_size: documentPageSize.value,
      knowledge_base_id: filters.value.knowledge_base_id || undefined,
      classification: filters.value.classification || undefined,
      sensitivity_level: filters.value.sensitivity_level || undefined,
      q: filters.value.q || undefined,
    }, { signal }),
    staleTime: 0,
    retry: 1,
  })

  const documents = computed(() => documentsQuery.data.value?.items || [])
  const documentTotal = computed(() => documentsQuery.data.value?.total || 0)
  const knowledgeBases = computed(() => knowledgeBasesQuery.data.value || [])

  const fetchDocuments = async () => documentsQuery.refetch()
  const fetchKnowledgeBases = async () => knowledgeBasesQuery.refetch()

  const handleDocumentPageChange = async (page) => {
    documentPage.value = page
    await fetchDocuments()
  }

  const handleFilterChange = async () => {
    documentPage.value = 1
    await fetchDocuments()
  }

  /** 把路由直达的文档插入当前列表缓存（保持侧栏可见性） */
  function prependDocument(normalized) {
    const key = qk.documents.list({
      page: documentPage.value,
      pageSize: documentPageSize.value,
      filters: filters.value,
    })
    const current = getQueryData(key)
    const items = [normalized, ...(current?.items || []).filter((item) => item.id !== normalized.id)]
    setQueryData(key, { ...(current || {}), items, total: (current?.total || 0) + (current ? 0 : 1) })
  }

  const uploadMutation = useMutation({
    mutationFn: async (payload, ctx) => {
      if (payload.files.length > 1) {
        return api.batchUploadDocuments(payload.files, true, payload.options, { idempotencyKey: ctx.idempotencyKey })
      }
      return api.uploadDocument(payload.files[0], true, payload.options, { idempotencyKey: ctx.idempotencyKey })
    },
    invalidate: [qkPrefix('documents', 'list'), qkPrefix('documents', 'knowledge-bases')],
    onError: (error) => {
      ElMessage.error(error.message)
    },
  })

  const onFileChange = (_uploadFile, uploadFiles) => {
    selectedFiles.value = (uploadFiles || []).map((item) => item.raw).filter(Boolean)
    file.value = selectedFiles.value[0] || null
  }

  const buildUploadPayload = () => {
    const options = {
      knowledge_base_name: uploadForm.value.knowledge_base_name || undefined,
      classification: uploadForm.value.classification || undefined,
      tags: uploadForm.value.tags || undefined,
      sensitivity_level: uploadForm.value.sensitivity_level || undefined,
      permission_scope: uploadForm.value.permission_scope || undefined,
      permission_users: uploadForm.value.permission_users || undefined,
      permission_roles: uploadForm.value.permission_roles || undefined,
    }
    return { files: [...selectedFiles.value], options }
  }

  /**
   * 执行上传（幂等）；成功后通过 onUploaded 回调让上层编排后续动作。
   * @param {(firstDocument: any, count: number) => Promise<void>} onUploaded
   */
  const uploadAndAnalyze = async (onUploaded) => {
    if (!selectedFiles.value.length) return
    uploading.value = true
    try {
      const payload = buildUploadPayload()
      const { data } = await uploadMutation.mutate(payload)
      const firstDocument = data.documents?.[0] || data
      const count = data.count || (payload.files.length > 1 ? payload.files.length : 1)
      ElMessage.success(payload.files.length > 1 ? `已上传 ${count} 份文档` : '文档上传成功')
      selectedFiles.value = []
      file.value = null
      if (onUploaded) await onUploaded(firstDocument)
    } finally {
      uploading.value = false
    }
  }

  return {
    // state
    file,
    selectedFiles,
    uploading,
    uploadForm,
    filters,
    documentPage,
    documentPageSize,
    documents,
    documentTotal,
    knowledgeBases,
    classificationOptions,
    // actions
    fetchDocuments,
    fetchKnowledgeBases,
    handleDocumentPageChange,
    handleFilterChange,
    prependDocument,
    onFileChange,
    uploadAndAnalyze,
  }
}
