import { ref } from 'vue'
import { tokenDays } from './useSystemPeriod'

const emptyGovernance = () => ({ today: {}, rate_limit: {}, policy: {} })
const emptyTokenStats = () => ({ by_action: {}, by_date: {}, by_model: {}, governance: emptyGovernance() })
const emptyGlobalStats = () => ({ by_model: {}, governance: { today: {}, policy: {} } })
const emptyBilling = () => ({ summary: {}, by_model: {}, by_date: {}, pricing: { items: [] } })

export function useSystemTokenAnalytics({ client, message, isAdmin }) {
  const myStats = ref(emptyTokenStats()), globalStats = ref(emptyGlobalStats()), llmPage = ref(1), llmPageSize = ref(20), llmTotal = ref(0), llmStats = ref({}), llmCalls = ref([]), llmLoading = ref(false), billingStats = ref(emptyBilling()), qaReplayRows = ref([]), qaReplayVisible = ref(false), selectedQaReplay = ref(null)
  let fetchSeq = 0
  const fetchTokenData = async () => {
    const seq = ++fetchSeq
    llmLoading.value = true
    try {
      const scope = isAdmin.value ? 'all' : 'mine'
      const [mine, stats, calls, billing, replays, gstats] = await Promise.all([client.myTokenStats(tokenDays.value), client.llmCallStats({ days: tokenDays.value, scope }), client.listLlmCalls({ days: tokenDays.value, scope, page: llmPage.value, page_size: llmPageSize.value }), client.llmBillingStats({ days: tokenDays.value, scope }), client.listQaReplays({ days: tokenDays.value, scope, page: 1, page_size: 10 }), isAdmin.value ? client.globalTokenStats(tokenDays.value) : Promise.resolve(null)])
      if (seq !== fetchSeq) return
      myStats.value = { ...emptyTokenStats(), ...(mine.data || {}) }; llmStats.value = stats.data || {}; llmCalls.value = calls.data?.items || []; llmTotal.value = calls.data?.total || 0; billingStats.value = billing.data || emptyBilling(); qaReplayRows.value = replays.data?.items || []
      globalStats.value = isAdmin.value ? { ...emptyGlobalStats(), ...((gstats && gstats.data) || {}) } : emptyGlobalStats()
    } catch (error) {
      if (seq !== fetchSeq) return
      myStats.value = emptyTokenStats(); llmStats.value = {}; llmCalls.value = []; llmTotal.value = 0; billingStats.value = emptyBilling(); qaReplayRows.value = []; globalStats.value = emptyGlobalStats(); message.error(error.response?.data?.detail || '获取模型调用统计失败')
    } finally { llmLoading.value = false }
  }
  const openQaReplay = (row) => { selectedQaReplay.value = row; qaReplayVisible.value = true }
  const resetTokenPagination = async () => { llmPage.value = 1; await fetchTokenData() }
  const handleLlmPageChange = async (page) => { llmPage.value = page; await fetchTokenData() }
  return { tokenDays, myStats, globalStats, llmPage, llmPageSize, llmTotal, llmStats, llmCalls, llmLoading, billingStats, qaReplayRows, qaReplayVisible, selectedQaReplay, fetchTokenData, openQaReplay, resetTokenPagination, handleLlmPageChange }
}
