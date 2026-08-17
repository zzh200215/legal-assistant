import { ref } from 'vue'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'
import api from '../api'

// Agent「待审批操作」领域模块：pending 审批列表。
// 审批动作（通过/拒绝）在 useAgentExecution 中编排（通过走 WS 恢复、拒绝走 REST 幂等写）。

export function useAgentApprovals() {
  const approvalsQuery = useQuery({
    key: () => qk.agent.approvals({ status: 'pending' }),
    fetcher: ({ signal }) => api.listApprovals({ status: 'pending' }, { signal }),
    staleTime: 0,
    retry: 1,
  })

  const approvals = ref([])

  const fetchApprovals = async () => {
    await approvalsQuery.refetch()
    approvals.value = approvalsQuery.data.value || []
  }

  return {
    approvals,
    fetchApprovals,
  }
}
