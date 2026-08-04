import { ref } from 'vue'

export function useSystemFeedback({ client, message, router, isAdmin }) {
  const feedbackDays = ref(30)
  const feedbackScope = ref('mine')
  const feedbackValueFilter = ref(null)
  const feedbackStatusFilter = ref(null)
  const feedbackRows = ref([])
  const feedbackStats = ref({})
  const feedbackLoading = ref(false)
  const feedbackPage = ref(1)
  const feedbackPageSize = ref(20)
  const feedbackTotal = ref(0)
  const feedbackResolveVisible = ref(false)
  const selectedFeedback = ref(null)
  const feedbackResolutionNote = ref('')
  const resolvingFeedback = ref(false)
  const fetchFeedbackData = async () => {
    feedbackLoading.value = true
    try {
      const params = { days: feedbackDays.value, page: feedbackPage.value, page_size: feedbackPageSize.value, scope: isAdmin.value ? feedbackScope.value : 'mine' }
      if (feedbackValueFilter.value) params.feedback_value = feedbackValueFilter.value
      if (feedbackStatusFilter.value) params.feedback_status = feedbackStatusFilter.value
      const [listRes, statsRes] = await Promise.all([client.listFeedback(params), client.feedbackStats(params)])
      feedbackRows.value = listRes.data?.items || []; feedbackTotal.value = listRes.data?.total || 0; feedbackStats.value = statsRes.data || {}
    } catch (error) {
      feedbackRows.value = []; feedbackTotal.value = 0; feedbackStats.value = {}
      if (error.response?.status === 403) { message.warning(error.response?.data?.detail || '当前账号无权查看该范围反馈'); feedbackScope.value = 'mine' }
      else message.error(error.response?.data?.detail || '获取反馈数据失败')
    } finally { feedbackLoading.value = false }
  }
  const exportFeedbackBundle = async () => {
    try { const { data } = await client.exportFeedbackEvalBundle({ days: feedbackDays.value }); message.success(`已导出 ${data.count || 0} 条反馈到评测集`) }
    catch (error) { message.error(error.response?.data?.detail || '导出评测集失败') }
  }
  const resetFeedbackPagination = async () => { feedbackPage.value = 1; await fetchFeedbackData() }
  const handleFeedbackPageChange = async (page) => { feedbackPage.value = page; await fetchFeedbackData() }
  const openFeedbackTarget = (row) => { if (row.document_id) router.push({ path: '/documents', query: { documentId: String(row.document_id) } }) }
  const openFeedbackResolve = (row) => { selectedFeedback.value = row; feedbackResolutionNote.value = row.feedback_resolution_note || ''; feedbackResolveVisible.value = true }
  const submitFeedbackResolve = async () => {
    if (!selectedFeedback.value?.id) return
    resolvingFeedback.value = true
    try { await client.resolveFeedback(selectedFeedback.value.id, { resolution_note: feedbackResolutionNote.value || null }); feedbackResolveVisible.value = false; selectedFeedback.value = null; feedbackResolutionNote.value = ''; message.success('反馈已处理'); await fetchFeedbackData() }
    catch (error) { message.error(error.response?.data?.detail || '处理反馈失败') } finally { resolvingFeedback.value = false }
  }
  return { feedbackDays, feedbackScope, feedbackValueFilter, feedbackStatusFilter, feedbackRows, feedbackStats, feedbackLoading, feedbackPage, feedbackPageSize, feedbackTotal, feedbackResolveVisible, selectedFeedback, feedbackResolutionNote, resolvingFeedback, fetchFeedbackData, exportFeedbackBundle, resetFeedbackPagination, handleFeedbackPageChange, openFeedbackTarget, openFeedbackResolve, submitFeedbackResolve }
}
