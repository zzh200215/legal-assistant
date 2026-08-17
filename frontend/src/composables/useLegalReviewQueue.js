import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk } from '../query/keys'

// 律师审核 tab 领域模块（查询层 + 幂等写）：
// 审核队列/统计走统一查询层；审核动作、批注经 useMutation（Idempotency-Key 防重复审核）。

export function useLegalReviewQueue({ client, message, prompt, targetLabel }) {
  const reviewHistoryMap = ref({})
  const commentDraft = ref({})
  const commentLoading = ref({})

  const queueQuery = useQuery({
    key: qk.legal.reviewQueue(),
    fetcher: () => client.listLegalReviewQueue(),
    staleTime: 15 * 1000,
  })

  const statsQuery = useQuery({
    key: qk.legal.reviewStats(),
    fetcher: () => client.getReviewStats(),
    staleTime: 30 * 1000,
  })

  const reviewQueue = computed(() => queueQuery.data.value || [])
  const reviewStats = computed(() => statsQuery.data.value || null)

  const loadReviewQueue = async () => {
    await queueQuery.refetch()
  }

  const loadReviewStats = async () => {
    await statsQuery.refetch()
  }

  const reviewKey = (row) => `${row.target_type}:${row.id}`

  const onExpandReview = async (row) => {
    const key = reviewKey(row)
    if (reviewHistoryMap.value[key]) return
    try {
      const { data } = await client.getReviewHistory(row.target_type, row.id)
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: data.history || [] }
    } catch {
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: [] }
    }
  }

  const commentMutation = useMutation({
    mutationFn: (payload, ctx) => client.addReviewComment(payload.type, payload.id, payload.note, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result, variables) => {
      const key = reviewKey({ target_type: variables.type, id: variables.id })
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: [result.data, ...(reviewHistoryMap.value[key] || [])] }
      commentDraft.value = { ...commentDraft.value, [key]: '' }
      message.success('批注已发送')
    },
    onError: (error) => {
      message.error(error.message || '批注发送失败')
    },
  })

  const submitComment = async (row) => {
    const key = reviewKey(row)
    const note = (commentDraft.value[key] || '').trim()
    if (!note) return message.warning('请输入批注内容')
    commentLoading.value = { ...commentLoading.value, [key]: true }
    try {
      await commentMutation.mutate({ type: row.target_type, id: row.id, note })
    } finally {
      commentLoading.value = { ...commentLoading.value, [key]: false }
    }
  }

  const actionMutation = useMutation({
    mutationFn: (payload, ctx) => client.submitLegalReviewAction(payload.type, payload.id, payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qk.legal.reviewQueue(), qk.legal.reviewStats()],
    onSuccess: () => {
      message.success('操作成功')
    },
    onError: (error) => {
      message.error(error.message || '审核操作失败')
    },
  })

  const reviewAction = async (row, action) => {
    const labels = { approve: '通过', return: '退回补充', offline: '转线下', close: '关闭' }
    try {
      const { value: note } = await prompt('审核意见（可选）', `${labels[action]} - ${targetLabel(row.target_type)} #${row.id}`, {
        confirmButtonText: '确认', cancelButtonText: '取消', inputPlaceholder: '填写审核意见...',
      })
      await actionMutation.mutate({
        type: row.target_type,
        id: row.id,
        body: { action, note: note || null },
      })
    } catch {
      // 用户取消输入框：静默
    }
  }

  return {
    reviewQueue,
    reviewStats,
    reviewHistoryMap,
    commentDraft,
    commentLoading,
    loadReviewQueue,
    loadReviewStats,
    reviewKey,
    onExpandReview,
    submitComment,
    reviewAction,
  }
}
