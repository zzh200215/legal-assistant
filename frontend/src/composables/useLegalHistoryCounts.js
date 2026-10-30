import { computed } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { qk } from '../query/keys'

// 工作台首屏历史总数：咨询/合同审查/文书三个 COUNT 一次返回。
// 三个 tab 共用同一个 query key，切换 tab 时不重复打接口。
export function useLegalHistoryCounts({ client, caseId, enabled = true }) {
  const query = useQuery({
    key: () => qk.legal.historyCounts(caseId?.value || null),
    fetcher: () => client.getLegalHistoryCounts({
      params: caseId?.value ? { case_id: caseId.value } : undefined,
    }),
    staleTime: 30 * 1000,
    enabled,
  })
  return {
    historyCounts: computed(() => query.data.value || null),
    historyCountsQuery: query,
  }
}
