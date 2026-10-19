import { ref } from 'vue'
import { tokenDays } from './useSystemPeriod'
import { normalizeError } from '../api/errors.js'

const emptyHealth = () => ({ status: '', checks: {}, timestamp: '' })
const emptyExperiment = () => ({ artifact_status: {}, summary: {}, experiments: [], rollouts: { items: [] }, prompt_traffic: { items: [] } })
const emptyEvidence = () => ({
  window_days: 30,
  total_consultations: 0,
  with_evidence: 0,
  with_evidence_generated: 0,
  with_evidence_recomputed: 0,
  without_evidence: 0,
  evidence_coverage: null,
  levels: [],
  references: {},
  checked_rate: null,
  trend: [],
  sources: [],
  sources_total: 0,
  corpus_gaps: [],
  unmatched_references: { references: 0, consultations: 0 },
})

const emptyIndexStats = () => ({
  enabled: true,
  ttl_seconds: 300,
  index_ready: false,
  stale: true,
  epoch: 0,
  built_at: null,
  builds: 0,
  build_failures: 0,
  stale_discarded: 0,
  ttl_rebuilds: 0,
  fallback_scans: 0,
  last_build_ms: null,
  last_build_at: null,
  index_size: 0,
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
  const indexLoading = ref(false)
  const indexStats = ref(emptyIndexStats())
  const indexError = ref(null)

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
  const fetchIndexStats = async () => {
    indexError.value = null
    if (!isAdmin.value) { indexStats.value = emptyIndexStats(); return }
    indexLoading.value = true
    try { const { data } = await client.retrievalIndexStats(); indexStats.value = { ...emptyIndexStats(), ...(data || {}) } }
    catch (error) { indexStats.value = emptyIndexStats(); indexError.value = normalizeError(error); message.error(error.response?.data?.detail || '获取检索索引状态失败') }
    finally { indexLoading.value = false }
  }
  return { healthLoading, healthData, experimentDays, experimentLoading, experimentOverview, experimentError, evidenceLoading, evidenceStats, evidenceError, toolHealthLoading, toolHealthRows, toolHealthError, indexLoading, indexStats, indexError, fetchHealthData, fetchExperimentOverview, fetchEvidenceStats, fetchToolHealth, fetchIndexStats }
}
