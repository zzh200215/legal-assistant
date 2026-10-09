import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk, qkPrefix } from '../query/keys'

// 文书草稿 tab 领域模块（查询层 + 幂等写）：模板/草稿列表走统一查询层，生成草稿经 useMutation。

export function useLegalDrafts({ client, message, caseId }) {
  const draftForm = ref({ document_type: '', fields: {} })
  const draftLoading = ref(false)
  const draftResult = ref(null)
  const draftFieldMap = ref({})
  const draftVersionMap = ref({})
  const resubmitDraftForm = ref({})
  const resubmitLoading = ref({})
  const draftComments = ref([])
  const draftCollaboration = ref(null)

  const templatesQuery = useQuery({
    key: ['legal', 'templates'],
    fetcher: () => client.listLegalTemplates(),
    staleTime: 60 * 1000,
  })

  const draftsQuery = useQuery({
    key: () => qk.legal.drafts(caseId?.value || null),
    fetcher: () => client.listLegalDrafts({ params: caseId?.value ? { case_id: caseId.value } : undefined }),
    staleTime: 30 * 1000,
  })

  const templates = computed(() => templatesQuery.data.value || [])
  const drafts = computed(() => {
    const rows = draftsQuery.data.value || []
    const activeCaseId = caseId?.value
    return activeCaseId ? rows.filter((row) => Number(row.case_id) === Number(activeCaseId)) : rows
  })

  const loadTemplates = async () => {
    await templatesQuery.refetch()
  }

  const setTemplateFields = (fields) => {
    draftFieldMap.value = fields
  }

  const loadDrafts = async () => {
    await draftsQuery.refetch()
  }

  const submitMutation = useMutation({
    mutationFn: (payload, ctx) => client.createLegalDraft(payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('legal', 'drafts')],
    onSuccess: (result) => {
      draftResult.value = result.data
    },
    onError: (error) => {
      message.error(error.message || '生成失败')
    },
  })

  const submitDraft = async () => {
    if (!draftForm.value.document_type) return message.warning('请选择文书类型')
    draftLoading.value = true
    try {
      await submitMutation.mutate({
        body: {
          document_type: draftForm.value.document_type,
          fields: draftForm.value.fields,
          case_id: caseId?.value || undefined,
        },
      })
    } finally {
      draftLoading.value = false
    }
  }

  const loadDraftVersions = async (draftId) => {
    if (!draftId || draftVersionMap.value[draftId]) return
    try {
      const { data } = await client.listDraftVersions(draftId)
      draftVersionMap.value = { ...draftVersionMap.value, [draftId]: data || [] }
    } catch {
      draftVersionMap.value = { ...draftVersionMap.value, [draftId]: [] }
    }
  }

  const loadDraftComments = async (draftId) => {
    if (!draftId) return []
    try {
      const { data } = await client.listDraftComments(draftId)
      draftComments.value = data || []
    } catch {
      draftComments.value = []
    }
    return draftComments.value
  }

  const loadDraftCollaboration = async (draftId) => {
    if (!draftId) return null
    try {
      const { data } = await client.getDraftCollaboration(draftId)
      draftCollaboration.value = data
    } catch {
      draftCollaboration.value = null
    }
    return draftCollaboration.value
  }

  const loadDraftDiff = async (draftId, fromId, toId) => {
    if (!draftId || !fromId || !toId) return null
    const { data } = await client.diffDraftVersions(draftId, fromId, toId)
    return data
  }

  const addDraftComment = async ({ id, body, version, line_start, line_end, mentions = [] }) => {
    const result = await client.addDraftComment(id, { body, version, line_start, line_end, mentions })
    draftComments.value = [...draftComments.value, result.data]
    return result.data
  }

  const updateDraftComment = async ({ id, commentId, status }) => {
    const result = await client.updateDraftComment(id, commentId, { status })
    draftComments.value = draftComments.value.map((item) => item.id === commentId ? result.data : item)
    return result.data
  }

  const autosaveDraft = async ({ id, document_type, fields, content, base_row_version }) => {
    const result = await client.autosaveDraft(id, {
      document_type,
      fields: fields || {},
      content: content || '',
      base_row_version,
    })
    draftResult.value = result.data
    return result.data
  }

  const saveDraftVersion = async ({ id, document_type, fields, content, base_row_version, version_note }) => {
    const result = await client.saveDraftVersion(id, {
      document_type,
      fields: fields || {},
      content: content || '',
      base_row_version,
      version_note,
    })
    draftResult.value = result.data
    draftVersionMap.value = { ...draftVersionMap.value, [id]: undefined }
    message.success(`已保存 Version ${result.data.version || 1}`)
    return result.data
  }

  const restoreDraftVersion = async ({ id, versionId, base_row_version }) => {
    const result = await client.restoreDraftVersion(id, versionId, { base_row_version })
    draftResult.value = result.data
    draftVersionMap.value = { ...draftVersionMap.value, [id]: undefined }
    message.success(`已恢复为 Version ${result.data.version || 1}`)
    return result.data
  }

  const resubmitMutation = useMutation({
    mutationFn: (payload, ctx) => client.resubmitDraft(payload.id, {
      document_type: payload.document_type,
      fields: payload.fields || {},
      content: payload.content,
    }, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('legal', 'drafts')],
    onSuccess: (result, variables) => {
      draftResult.value = result.data
      resubmitDraftForm.value = { ...resubmitDraftForm.value, [variables.id]: '' }
      draftVersionMap.value = { ...draftVersionMap.value, [variables.id]: undefined }
      message.success(`已保存 Version ${result.data.version || 1}`)
    },
    onError: (error) => message.error(error.message || '保存版本失败'),
  })

  const resubmitDraft = async (draft) => {
    const content = (resubmitDraftForm.value[draft.id] || draft.content || '').trim()
    if (!content) return message.warning('请输入文书正文')
    resubmitLoading.value = { ...resubmitLoading.value, [draft.id]: true }
    try {
    const result = await resubmitMutation.mutate({
      id: draft.id,
      document_type: draft.document_type,
      fields: draft.fields || {},
      content,
    })
    return result?.data || draftResult.value
    } finally {
      resubmitLoading.value = { ...resubmitLoading.value, [draft.id]: false }
    }
  }

  return {
    templates,
    draftForm,
    draftLoading,
    draftResult,
    drafts,
    draftFieldMap,
    draftVersionMap,
    draftComments,
    draftCollaboration,
    resubmitDraftForm,
    resubmitLoading,
    loadTemplates,
    setTemplateFields,
    loadDrafts,
    submitDraft,
    loadDraftVersions,
    loadDraftComments,
    loadDraftCollaboration,
    loadDraftDiff,
    addDraftComment,
    updateDraftComment,
    autosaveDraft,
    saveDraftVersion,
    restoreDraftVersion,
    resubmitDraft,
  }
}
