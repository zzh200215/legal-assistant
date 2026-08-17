import { ref } from 'vue'
import { tokenDays } from './useSystemPeriod'

const emptyHealth = () => ({ status: '', checks: {}, timestamp: '' })
const emptyExperiment = () => ({ artifact_status: {}, summary: {}, experiments: [], rollouts: { items: [] }, prompt_traffic: { items: [] } })

export function useSystemObservability({ client, message, isAdmin }) {
  const healthLoading = ref(false)
  const healthData = ref(emptyHealth())
  const experimentDays = ref(30)
  const experimentLoading = ref(false)
  const experimentOverview = ref(emptyExperiment())
  const toolHealthLoading = ref(false)
  const toolHealthRows = ref([])

  const fetchHealthData = async () => {
    healthLoading.value = true
    try { const { data } = await client.healthCheck(); healthData.value = data || emptyHealth() }
    catch (error) { healthData.value = { ...emptyHealth(), status: 'error' }; message.error(error.response?.data?.detail || '获取健康检查失败') }
    finally { healthLoading.value = false }
  }
  const fetchExperimentOverview = async () => {
    if (!isAdmin.value) { experimentOverview.value = emptyExperiment(); return }
    experimentLoading.value = true
    try { const { data } = await client.experimentOverview(experimentDays.value); experimentOverview.value = data || emptyExperiment() }
    catch (error) { experimentOverview.value = emptyExperiment(); message.error(error.response?.data?.detail || '获取实验观测失败') }
    finally { experimentLoading.value = false }
  }
  const fetchToolHealth = async () => {
    toolHealthLoading.value = true
    try { const { data } = await client.toolHealth({ days: tokenDays.value, scope: isAdmin.value ? 'all' : 'mine' }); toolHealthRows.value = data?.items || [] }
    catch (error) { toolHealthRows.value = []; message.error(error.response?.data?.detail || '获取工具健康失败') }
    finally { toolHealthLoading.value = false }
  }
  return { healthLoading, healthData, experimentDays, experimentLoading, experimentOverview, toolHealthLoading, toolHealthRows, fetchHealthData, fetchExperimentOverview, fetchToolHealth }
}
