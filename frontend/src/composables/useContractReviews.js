import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk } from '../query/keys'

// 合同审查 tab 领域模块（查询层 + 幂等写）：
// 审查列表走统一查询层；提交审查/文件审查/重新提交经 useMutation（Idempotency-Key 防连点重复）。

export function useContractReviews({ client, message, caseId }) {
  const contractForm = ref({ title: '', content: '' })
  const contractLoading = ref(false)
  const contractResult = ref(null)
  const uploadLoading = ref(false)
  const contractVersionMap = ref({})
  const resubmitDraftForm = ref({})
  const resubmitLoading = ref({})

  const reviewsQuery = useQuery({
    key: qk.legal.contractReviews(),
    fetcher: () => client.listContractReviews(),
    staleTime: 30 * 1000,
  })

  const contractReviews = computed(() => reviewsQuery.data.value || [])

  const loadContractReviews = async () => {
    await reviewsQuery.refetch()
  }

  const submitMutation = useMutation({
    mutationFn: (payload, ctx) => client.createContractReview(payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qk.legal.contractReviews()],
    onSuccess: (result, variables) => {
      contractResult.value = result.data
      if (variables.onResult) variables.onResult()
    },
    onError: (error) => {
      message.error(error.message || '审查失败')
    },
  })

  const submitContractReview = async (onResult) => {
    if (!contractForm.value.content.trim()) return message.warning('请输入合同内容')
    contractLoading.value = true
    try {
      await submitMutation.mutate({
        body: {
          ...contractForm.value,
          case_id: caseId?.value || undefined,
        },
        onResult,
      })
    } finally {
      contractLoading.value = false
    }
  }

  const uploadMutation = useMutation({
    mutationFn: (payload, ctx) => client.uploadContractReview(payload.file, payload.title, payload.caseId, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qk.legal.contractReviews()],
    onSuccess: (result, variables) => {
      contractResult.value = result.data
      contractForm.value.title = result.data.title || contractForm.value.title
      if (variables.onResult) variables.onResult()
      message.success('合同文件审查完成')
    },
    onError: (error) => {
      message.error(error.message || '文件审查失败')
    },
  })

  const handleContractUpload = async (file, onResult) => {
    uploadLoading.value = true
    try {
      await uploadMutation.mutate({
        file,
        title: contractForm.value.title || undefined,
        caseId: caseId?.value || undefined,
        onResult,
      })
    } finally {
      uploadLoading.value = false
    }
    return false
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

  const resubmitMutation = useMutation({
    mutationFn: (payload, ctx) => client.resubmitContractReview(payload.id, payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qk.legal.contractReviews()],
    onSuccess: (result, variables) => {
      message.success(`已重新提交，版本升级为 v${result.data.version}`)
      resubmitDraftForm.value = { ...resubmitDraftForm.value, [variables.id]: '' }
      contractVersionMap.value = { ...contractVersionMap.value, [variables.id]: undefined }
    },
    onError: (error) => {
      message.error(error.message || '重新提交失败')
    },
  })

  const submitContractResubmit = async (row) => {
    const newContent = (resubmitDraftForm.value[row.id] || '').trim()
    if (!newContent) return message.warning('请输入修改后的合同内容')
    resubmitLoading.value = { ...resubmitLoading.value, [row.id]: true }
    try {
      await resubmitMutation.mutate({
        id: row.id,
        body: { title: row.title, content: newContent },
      })
    } finally {
      resubmitLoading.value = { ...resubmitLoading.value, [row.id]: false }
    }
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
