import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'
import api from '../api'

// Agent「运行历史」领域模块：分页列表（服务端分页）。
// 历史列表经统一查询层（同 key 去重、取消、缓存），页码变化自动切换 key。

export function useAgentHistory() {
  const historyPage = ref(1)
  const historyPageSize = ref(10)
  const historyLoading = ref(false)

  const historyQuery = useQuery({
    key: () => qk.agent.runs({ page: historyPage.value, pageSize: historyPageSize.value }),
    fetcher: ({ signal }) => api.listAgentRuns({
      page: historyPage.value,
      page_size: historyPageSize.value,
    }, { signal }),
    staleTime: 0,
    retry: 1,
  })

  const history = computed(() => historyQuery.data.value?.items || [])
  const historyTotal = computed(() => historyQuery.data.value?.total || 0)
  const historyError = computed(() => historyQuery.error.value)

  const fetchHistory = async () => {
    historyLoading.value = true
    try {
      await historyQuery.refetch()
    } finally {
      historyLoading.value = false
    }
  }

  const handleHistoryPageChange = async (page) => {
    historyPage.value = page
    await fetchHistory()
  }

  return {
    history,
    historyTotal,
    historyLoading,
    historyError,
    historyPage,
    historyPageSize,
    fetchHistory,
    handleHistoryPageChange,
  }
}
