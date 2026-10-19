import { ref } from 'vue'
import { tokenDays } from './useSystemPeriod'
import { normalizeError } from '../api/errors.js'

const emptyHealth = () => ({ status: '', checks: {}, timestamp: '' })
const emptyExperiment = () => ({ artifact_status: {}, summary: {}, experiments: [], rollouts: { items: [] }, prompt_traffic: { items: [] } })
const emptyEvidence = () => ({
  window_days: 30,
  total_consultations: 0,
  with_evidence: 0,
  without_evidence: 0,
  evidence_coverage: null,
  levels: [],
  references: {},
  checked_rate: null,
})

export function useSystemObservability({ client, message, isAdmin }) {
  const healthLoading = ref(false)
  const healthData = ref(emptyHealth())
  const experimentDays = ref(30)
  const experimentLoading = ref(false)
  const experimentOverview = ref(emptyExperiment())
  const experimentError = ref(null)
  const evidenceLoading = ref(false)
  const evidenceStats = ref(emptyEvidence())
  const evidenceError = ref(null)
  const toolHealthLoading = ref(false)
  const toolHealthRows = ref([])
  const toolHealthError = ref(null)

  const fetchHealthData = async () => {
    healthLoading.value = true
    try { const { data } = await client.healthCheck(); healthData.value = data || emptyHealth() }
    catch (error) { healthData.value = { ...emptyHealth(), status: 'error' }; message.error(error.response?.data?.detail || '获取健康检查失败') }
    finally { healthLoading.value = false }
  }
  const fetchExperimentOverview = async () => {
    experimentError.value = null
    if (!isAdmin.value) { experimentOverview.value = emptyExperiment(); return }
    experimentLoading.value = true
    try { const { data } = await client.experimentOverview(experimentDays.value); experimentOverview.value = data || emptyExperiment() }
    catch (error) { experimentOverview.value = emptyExperiment(); experimentError.value = normalizeError(error); message.error(error.response?.data?.detail || '获取实验观测失败') }
    finally { experimentLoading.value = false }
  }
  const fetchEvidenceStats = async () => {
    evidenceError.value = null
    if (!isAdmin.value) { evidenceStats.value = emptyEvidence(); return }
    evidenceLoading.value = true
    try { const { data } = await client.consultationEvidenceStats(experimentDays.value); evidenceStats.value = data || emptyEvidence() }
    catch (error) { evidenceStats.value = emptyEvidence(); evidenceError.value = normalizeError(error); message.error(error.response?.data?.detail || '获取证据可核验性失败') }
    finally { evidenceLoading.value = false }
  }
  const fetchToolHealth = async () => {
    toolHealthLoading.value = true
    toolHealthError.value = null
    try { const { data } = await client.toolHealth({ days: tokenDays.value, scope: isAdmin.value ? 'all' : 'mine' }); toolHealthRows.value = data?.items || [] }
    catch (error) { toolHealthRows.value = []; toolHealthError.value = normalizeError(error) }
    finally { toolHealthLoading.value = false }
  }
  return { healthLoading, healthData, experimentDays, experimentLoading, experimentOverview, experimentError, evidenceLoading, evidenceStats, evidenceError, toolHealthLoading, toolHealthRows, toolHealthError, fetchHealthData, fetchExperimentOverview, fetchEvidenceStats, fetchToolHealth }
}
