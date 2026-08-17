import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'
import api from '../api'

// Agent「指标 + 专家注册表」领域模块：概览指标与专家角色目录。

export function useAgentMeta() {
  const agentMetrics = ref({})
  const supervisorRole = ref(null)
  const agentRegistry = ref([])

  const metricsQuery = useQuery({
    key: () => qk.agent.metrics(30),
    fetcher: ({ signal }) => api.getAgentMetrics(30, { signal }),
    staleTime: 30 * 1000,
    retry: 1,
  })

  const registryQuery = useQuery({
    key: qk.agent.registry(),
    fetcher: ({ signal }) => api.getAgentRegistry({ signal }),
    staleTime: 60 * 1000,
    retry: 1,
  })

  const fetchAgentMetrics = async () => {
    await metricsQuery.refetch()
    agentMetrics.value = metricsQuery.data.value || {}
  }

  const fetchAgentRegistry = async () => {
    await registryQuery.refetch()
    const data = registryQuery.data.value
    supervisorRole.value = data?.supervisor || null
    agentRegistry.value = data?.items || []
  }

  const expertRoles = computed(() => [
    ...(supervisorRole.value ? [supervisorRole.value] : []),
    ...agentRegistry.value,
  ])

  return {
    agentMetrics,
    supervisorRole,
    agentRegistry,
    expertRoles,
    fetchAgentMetrics,
    fetchAgentRegistry,
  }
}
