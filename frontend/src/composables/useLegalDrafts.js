import { ref } from 'vue'

// Owns legal-document template, draft-generation, and draft-history requests.
export function useLegalDrafts({ client, message, caseId }) {
  const templates = ref([])
  const draftForm = ref({ document_type: '', fields: {} })
  const draftLoading = ref(false)
  const draftResult = ref(null)
  const drafts = ref([])
  const draftFieldMap = ref({})

  const loadTemplates = async () => {
    try {
      const { data } = await client.listLegalTemplates()
      templates.value = data
    } catch {}
  }

  const setTemplateFields = (fields) => {
    draftFieldMap.value = fields
  }

  const loadDrafts = async () => {
    try {
      const { data } = await client.listLegalDrafts()
      drafts.value = data
    } catch {}
  }

  const submitDraft = async () => {
    if (!draftForm.value.document_type) return message.warning('请选择文书类型')
    draftLoading.value = true
    try {
      const { data } = await client.createLegalDraft({
        document_type: draftForm.value.document_type,
        fields: draftForm.value.fields,
        case_id: caseId?.value || undefined,
      })
      draftResult.value = data
      await loadDrafts()
    } catch (error) {
      message.error(error.response?.data?.detail || '生成失败')
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
