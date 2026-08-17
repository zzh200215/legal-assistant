import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk } from '../query/keys'

// 文书草稿 tab 领域模块（查询层 + 幂等写）：模板/草稿列表走统一查询层，生成草稿经 useMutation。

export function useLegalDrafts({ client, message, caseId }) {
  const draftForm = ref({ document_type: '', fields: {} })
  const draftLoading = ref(false)
  const draftResult = ref(null)
  const draftFieldMap = ref({})

  const templatesQuery = useQuery({
    key: ['legal', 'templates'],
    fetcher: () => client.listLegalTemplates(),
    staleTime: 60 * 1000,
  })

  const draftsQuery = useQuery({
    key: qk.legal.drafts(),
    fetcher: () => client.listLegalDrafts(),
    staleTime: 30 * 1000,
  })

  const templates = computed(() => templatesQuery.data.value || [])
  const drafts = computed(() => draftsQuery.data.value || [])

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
    invalidate: [qk.legal.drafts()],
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

  return {
    templates,
    draftForm,
    draftLoading,
    draftResult,
    drafts,
    draftFieldMap,
    loadTemplates,
    setTemplateFields,
    loadDrafts,
    submitDraft,
  }
}
