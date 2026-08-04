import { ref, computed } from 'vue'

export function useSystemFunnel({ client, message }) {
  const funnelDays = ref(30)
  const funnelData = ref(null)
  const funnelLoading = ref(false)

  const fetchFunnel = async () => {
    funnelLoading.value = true
    try {
      const resp = await client.dashboardFunnel(funnelDays.value)
      funnelData.value = resp.data?.data || null
    } catch (error) {
      funnelData.value = null
      message.error(error.response?.data?.detail || '获取用户漏斗失败')
    } finally {
      funnelLoading.value = false
    }
  }

  const funnelRows = computed(() =>
    (funnelData.value?.funnel || []).map((row) => {
      const registered = funnelData.value?.cohort?.registered || 0
      return {
        ...row,
        overall_pct: Math.round((row.overall_rate || 0) * 1000) / 10,
        hop_pct: Math.round((row.hop_rate || 0) * 1000) / 10,
        width: registered ? Math.round((row.users / registered) * 100) : 0,
      }
    }),
  )

  const funnelUpgradedUsers = computed(() => {
    const row = (funnelData.value?.funnel || []).find((item) => item.stage === 'upgraded')
    return row?.users ?? 0
  })

  return { funnelDays, funnelData, funnelLoading, funnelRows, funnelUpgradedUsers, fetchFunnel }
}
