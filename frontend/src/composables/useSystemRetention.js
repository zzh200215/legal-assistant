import { ref } from 'vue'

export function useSystemRetention({ client, message }) {
  const retentionDays = ref(90)
  const retentionData = ref(null)
  const retentionLoading = ref(false)

  const northStarWeeks = ref(12)
  const northStarData = ref(null)
  const northStarLoading = ref(false)

  const fetchRetention = async () => {
    retentionLoading.value = true
    try {
      const resp = await client.dashboardRetention(retentionDays.value)
      retentionData.value = resp.data?.data || null
    } catch (error) {
      retentionData.value = null
      message.error(error.response?.data?.detail || '获取留存数据失败')
    } finally {
      retentionLoading.value = false
    }
  }

  const fetchNorthStar = async () => {
    northStarLoading.value = true
    try {
      const resp = await client.dashboardNorthStar(northStarWeeks.value)
      northStarData.value = resp.data?.data || null
    } catch (error) {
      northStarData.value = null
      message.error(error.response?.data?.detail || '获取北极星指标失败')
    } finally {
      northStarLoading.value = false
    }
  }

  return {
    retentionDays, retentionData, retentionLoading, fetchRetention,
    northStarWeeks, northStarData, northStarLoading, fetchNorthStar,
  }
}
