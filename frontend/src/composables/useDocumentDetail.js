import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useQuery } from '../query/useQuery.js'
import { qk } from '../query/keys'
import { errorMessage } from '../api/errors'
import api from '../api'

// 文档「当前文档详情」领域模块：选中文档、元数据、版本、解析任务、问答记录、关联 Agent 执行、受控下载。
// 各子列表均按 docId 作为 query key（切换文档自动取消旧请求、不覆盖新状态）。

export function useDocumentDetail() {
  const docId = ref(null)
  const docMeta = ref(null)
  const downloading = ref(false)
  const downloadPolicySaving = ref(false)

  const versionsQuery = useQuery({
    key: () => qk.documents.versions(docId.value),
    fetcher: ({ signal }) => api.listDocumentVersions(docId.value, { signal }),
    enabled: () => docId.value != null,
  })
  const parseJobsQuery = useQuery({
    key: () => qk.documents.parseJobs(docId.value),
    fetcher: ({ signal }) => api.listDocumentParseJobs(docId.value, { signal }),
    enabled: () => docId.value != null,
  })
  const qaRecordsQuery = useQuery({
    key: () => qk.documents.qaRecords(docId.value),
    fetcher: ({ signal }) => api.listDocumentQaRecords(docId.value, { signal }),
    enabled: () => docId.value != null,
  })
  const relatedRunsQuery = useQuery({
    key: () => qk.documents.relatedRuns(docId.value),
    fetcher: ({ signal }) => api.listAgentRuns({ artifact_type: 'document', artifact_id: docId.value, page: 1, page_size: 5 }, { signal }),
    enabled: () => docId.value != null,
  })

  const versions = computed(() => versionsQuery.data.value?.items || [])
  const parseJobs = computed(() => parseJobsQuery.data.value?.items || [])
  const qaRecords = computed(() => (qaRecordsQuery.data.value?.items || []).map((item) => ({
    ...item,
    citations: parseJsonArray(item.citations),
  })))
  const relatedAgentRuns = computed(() => relatedRunsQuery.data.value?.items || [])

  const fetchVersions = async () => versionsQuery.refetch()
  const fetchParseJobs = async () => parseJobsQuery.refetch()
  const fetchQaRecords = async () => qaRecordsQuery.refetch()
  const fetchRelatedAgentRuns = async () => relatedRunsQuery.refetch()

  /** 并行等待四个子列表查询落地（selectDocument 后供 runAnalysis 等使用） */
  async function settleDetailFetches() {
    await Promise.all([
      versionsQuery.refetch(),
      parseJobsQuery.refetch(),
      qaRecordsQuery.refetch(),
      relatedRunsQuery.refetch(),
    ])
  }

  function parseJsonArray(value) {
    if (!value) return []
    if (Array.isArray(value)) return value
    try {
      const parsed = JSON.parse(value)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }

  const hydrateDocumentMeta = async (documentId) => {
    if (!documentId) return
    try {
      const { data } = await api.getDocument(documentId)
      docMeta.value = {
        ...docMeta.value,
        id: data.id,
        title: data.title,
        file_type: data.file_type,
        knowledge_base_id: data.knowledge_base_id,
        version_number: data.version_number,
        classification: data.classification,
        sensitivity_level: data.sensitivity_level,
        permission_scope: data.permission_scope,
        download_enabled: data.download_enabled !== false,
        watermark_required: Boolean(data.watermark_required),
        status: data.status,
        summary: data.summary,
        created_at: data.created_at,
        metadata_json: data.metadata_json || null,
      }
    } catch {
      // 详情元数据拉取失败不阻塞其他功能（保留列表项信息）
    }
  }

  /** 选中文档：设置 docId 并并行加载四个子列表 + 元数据 */
  async function selectDocument(item) {
    docId.value = item.id
    docMeta.value = item
    await Promise.all([hydrateDocumentMeta(item.id), settleDetailFetches()])
  }

  /** 上传完成后切换到新文档（docMeta 直接来自上传响应，子列表走查询） */
  async function setUploadedDocument(firstDocument) {
    docId.value = firstDocument.id
    docMeta.value = firstDocument
    await Promise.all([hydrateDocumentMeta(firstDocument.id), settleDetailFetches()])
  }

  const downloadFilename = (response) => {
    const disposition = response.headers?.['content-disposition'] || ''
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
    const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
    if (encoded) return decodeURIComponent(encoded)
    if (plain) return plain
    const extension = docMeta.value?.file_type ? `.${String(docMeta.value.file_type).replace(/^\./, '')}` : ''
    return `${docMeta.value?.title || 'document'}${extension}`
  }

  const downloadCurrentDocument = async () => {
    if (!docId.value || downloading.value) return
    downloading.value = true
    try {
      const response = await api.downloadDocument(docId.value)
      const url = URL.createObjectURL(response.data)
      const anchor = window.document.createElement('a')
      anchor.href = url
      anchor.download = downloadFilename(response)
      anchor.style.display = 'none'
      window.document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      ElMessage.success('受控下载已开始')
    } catch (error) {
      ElMessage.error(errorMessage(error) || '文档下载失败')
    } finally {
      downloading.value = false
    }
  }

  const updateDownloadPolicy = async (field, value) => {
    if (!docId.value || downloadPolicySaving.value) return
    const previous = field === 'download_enabled'
      ? docMeta.value.download_enabled !== false
      : Boolean(docMeta.value.watermark_required)
    if (field === 'download_enabled' && value === false) {
      try {
        await ElMessageBox.confirm('关闭后，所有有查看权限的用户都无法下载该文档。', '确认禁止下载', {
          confirmButtonText: '禁止下载',
          cancelButtonText: '取消',
          type: 'warning',
        })
      } catch {
        docMeta.value = { ...docMeta.value, [field]: previous }
        return
      }
    }
    downloadPolicySaving.value = true
    try {
      const { data } = await api.updateDocumentDownloadPolicy(docId.value, { [field]: value })
      docMeta.value = { ...docMeta.value, ...data }
      ElMessage.success(field === 'download_enabled' ? '下载策略已更新' : '水印策略已更新')
    } catch (error) {
      docMeta.value = { ...docMeta.value, [field]: previous }
      ElMessage.error(errorMessage(error) || '下载策略更新失败')
    } finally {
      downloadPolicySaving.value = false
    }
  }

  return {
    docId,
    docMeta,
    versions,
    parseJobs,
    qaRecords,
    relatedAgentRuns,
    downloading,
    downloadPolicySaving,
    selectDocument,
    setUploadedDocument,
    hydrateDocumentMeta,
    settleDetailFetches,
    fetchVersions,
    fetchParseJobs,
    fetchQaRecords,
    fetchRelatedAgentRuns,
    downloadCurrentDocument,
    updateDownloadPolicy,
  }
}