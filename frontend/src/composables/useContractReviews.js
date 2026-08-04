import { ref } from 'vue'

// Owns contract-review requests, versions, and resubmission state.
export function useContractReviews({ client, message, caseId }) {
  const contractForm = ref({ title: '', content: '' })
  const contractLoading = ref(false)
  const contractResult = ref(null)
  const contractReviews = ref([])
  const uploadLoading = ref(false)
  const contractVersionMap = ref({})
  const resubmitDraftForm = ref({})
  const resubmitLoading = ref({})

  const loadContractReviews = async () => {
    try {
      const { data } = await client.listContractReviews()
      contractReviews.value = data
    } catch {}
  }

  const submitContractReview = async (onResult) => {
    if (!contractForm.value.content.trim()) return message.warning('请输入合同内容')
    contractLoading.value = true
    try {
      const { data } = await client.createContractReview({
        ...contractForm.value,
        case_id: caseId?.value || undefined,
      })
      contractResult.value = data
      onResult()
      await loadContractReviews()
    } catch (error) {
      message.error(error.response?.data?.detail || '审查失败')
    } finally {
      contractLoading.value = false
    }
  }

  const onExpandContractReview = async (row) => {
    if (contractVersionMap.value[row.id]) return
    try {
      const { data } = await client.listContractReviewVersions(row.id)
      contractVersionMap.value = { ...contractVersionMap.value, [row.id]: data }
    } catch {
      contractVersionMap.value = { ...contractVersionMap.value, [row.id]: [] }
    }
  }

  const submitContractResubmit = async (row) => {
    const newContent = (resubmitDraftForm.value[row.id] || '').trim()
    if (!newContent) return message.warning('请输入修改后的合同内容')
    resubmitLoading.value = { ...resubmitLoading.value, [row.id]: true }
    try {
      const { data } = await client.resubmitContractReview(row.id, { title: row.title, content: newContent })
      message.success(`已重新提交，版本升级为 v${data.version}`)
      resubmitDraftForm.value = { ...resubmitDraftForm.value, [row.id]: '' }
      contractVersionMap.value = { ...contractVersionMap.value, [row.id]: undefined }
      await loadContractReviews()
    } catch (error) {
      message.error(error.response?.data?.detail || '重新提交失败')
    } finally {
      resubmitLoading.value = { ...resubmitLoading.value, [row.id]: false }
    }
  }

  const handleContractUpload = async (file, onResult) => {
    uploadLoading.value = true
    try {
      const { data } = await client.uploadContractReview(
        file,
        contractForm.value.title || undefined,
        caseId?.value || undefined,
      )
      contractResult.value = data
      contractForm.value.title = data.title || contractForm.value.title
      onResult()
      await loadContractReviews()
      message.success('合同文件审查完成')
    } catch (error) {
      message.error(error.response?.data?.detail || '文件审查失败')
    } finally {
      uploadLoading.value = false
    }
    return false
  }

  return {
    contractForm,
    contractLoading,
    contractResult,
    contractReviews,
    uploadLoading,
    contractVersionMap,
    resubmitDraftForm,
    resubmitLoading,
    loadContractReviews,
    submitContractReview,
    onExpandContractReview,
    submitContractResubmit,
    handleContractUpload,
  }
}
