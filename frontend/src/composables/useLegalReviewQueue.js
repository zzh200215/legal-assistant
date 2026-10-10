import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk, qkPrefix } from '../query/keys'

// 律师审核 tab 领域模块（查询层 + 幂等写）：
// 审核队列/统计走统一查询层；审核动作、批注经 useMutation（Idempotency-Key 防重复审核）。

// ElMessageBox 取消 reject 值；用户主动取消不算错误
const isUserCancel = (error) => error === 'cancel' || error === 'close' || error?.message === 'cancel'

export function useLegalReviewQueue({ client, message, prompt, targetLabel, caseId, canReview = null, filters = null }) {
  const reviewHistoryMap = ref({})
  const commentDraft = ref({})
  const commentLoading = ref({})
  const reviewers = ref([])

  const queueQuery = useQuery({
    key: () => qk.legal.reviewQueue(caseId?.value || null),
    fetcher: () => client.listLegalReviewQueue({ params: {
      ...(caseId?.value ? { case_id: caseId.value } : {}),
      ...(filters?.value?.status ? { status: filters.value.status } : {}),
      ...(filters?.value?.overdue !== '' && filters?.value?.overdue !== undefined ? { overdue: filters.value.overdue } : {}),
      ...(filters?.value?.search ? { search: filters.value.search } : {}),
    } }),
    staleTime: 15 * 1000,
  })

  const statsQuery = useQuery({
    key: qk.legal.reviewStats(),
    fetcher: () => client.getReviewStats(),
    staleTime: 30 * 1000,
    enabled: () => (canReview ? canReview.value : true),
  })

  const reviewQueue = computed(() => queueQuery.data.value || [])
  const reviewStats = computed(() => statsQuery.data.value || null)

  const loadReviewQueue = async () => {
    await queueQuery.refetch()
  }

  const bulkAssignReview = async (items, payload) => {
    const result = await client.bulkAssignReview({ items, ...payload })
    await loadReviewQueue()
    message.success(`已更新 ${result.data?.length || items.length} 项审核分配`)
    return result.data
  }

  const bulkReviewAction = async (items, action, note = null) => {
    const result = await client.bulkReviewAction({ items, action, note })
    await Promise.all([loadReviewQueue(), loadReviewStats()])
    message.success(`已处理 ${result.data?.length || items.length} 项审核`)
    return result.data
  }

  const loadReviewStats = async () => {
    await statsQuery.refetch()
  }

  const loadReviewers = async () => {
    try {
      const { data } = await client.listReviewers()
      reviewers.value = data || []
    } catch (error) {
      reviewers.value = []
      console.error('[review-queue] 审核人列表加载失败', error)
    }
  }

  const assignReview = async (row, payload) => {
    await client.assignReview(row.target_type, row.id, payload)
    await loadReviewQueue()
    message.success(payload.reviewer_id ? '审核任务已分配' : '已取消审核分配')
  }

  const reviewKey = (row) => `${row.target_type}:${row.id}`

  const onExpandReview = async (row) => {
    const key = reviewKey(row)
    if (reviewHistoryMap.value[key]) return
    try {
      const { data } = await client.getReviewHistory(row.target_type, row.id)
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: data.history || [] }
    } catch (error) {
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: [] }
      console.error('[review-queue] 审核历史加载失败', error)
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
    invalidate: [qkPrefix('legal', 'review-queue'), qk.legal.reviewStats()],
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
    } catch (error) {
      // 用户取消输入框：静默。API 错误由 actionMutation 的 onError 统一弹提示，此处不重复
      if (!isUserCancel(error)) console.error('[review-queue] 审核操作异常', error)
    }
  }

  return {
    reviewQueue,
    reviewStats,
    reviewers,
    reviewHistoryMap,
    commentDraft,
    commentLoading,
    loadReviewQueue,
    bulkAssignReview,
    bulkReviewAction,
    loadReviewStats,
    loadReviewers,
    assignReview,
    reviewKey,
    onExpandReview,
    submitComment,
    reviewAction,
  }
}
