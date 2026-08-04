import { ref } from 'vue'

export function useLegalReviewQueue({ client, message, prompt, targetLabel }) {
  const reviewQueue = ref([])
  const reviewStats = ref(null)
  const reviewHistoryMap = ref({})
  const commentDraft = ref({})
  const commentLoading = ref({})

  const loadReviewQueue = async () => {
    try {
      const { data } = await client.listLegalReviewQueue()
      reviewQueue.value = data
    } catch {}
  }

  const loadReviewStats = async () => {
    try {
      const { data } = await client.getReviewStats()
      reviewStats.value = data
    } catch {}
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

  const submitComment = async (row) => {
    const key = reviewKey(row)
    const note = (commentDraft.value[key] || '').trim()
    if (!note) return message.warning('请输入批注内容')
    commentLoading.value = { ...commentLoading.value, [key]: true }
    try {
      const { data } = await client.addReviewComment(row.target_type, row.id, note)
      reviewHistoryMap.value = { ...reviewHistoryMap.value, [key]: [data, ...(reviewHistoryMap.value[key] || [])] }
      commentDraft.value = { ...commentDraft.value, [key]: '' }
      message.success('批注已发送')
    } catch (error) {
      message.error(error.response?.data?.detail || '批注发送失败')
    } finally {
      commentLoading.value = { ...commentLoading.value, [key]: false }
    }
  }

  const reviewAction = async (row, action) => {
    const labels = { approve: '通过', return: '退回补充', offline: '转线下', close: '关闭' }
    try {
      const { value: note } = await prompt('审核意见（可选）', `${labels[action]} - ${targetLabel(row.target_type)} #${row.id}`, {
        confirmButtonText: '确认', cancelButtonText: '取消', inputPlaceholder: '填写审核意见...',
      })
      await client.submitLegalReviewAction(row.target_type, row.id, { action, note: note || null })
      message.success('操作成功')
      await Promise.all([loadReviewQueue(), loadReviewStats()])
    } catch {}
  }

  return {
    reviewQueue, reviewStats, reviewHistoryMap, commentDraft, commentLoading,
    loadReviewQueue, loadReviewStats, reviewKey, onExpandReview, submitComment, reviewAction,
  }
}
