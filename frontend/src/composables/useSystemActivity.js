import { ref } from 'vue'
import { normalizeError } from '../api/errors.js'

// 操作日志/告警为模块级单例：任务重试后会触发告警刷新（任务中心 tab 经 fetchAlerts 复用），
// 日志与告警两个 tab 以及任务中心共享同一份状态，避免拆分后各自持有副本。
const logDays = ref(30), logModule = ref(null), logScope = ref('mine'), logs = ref([]), logStats = ref({}), logLoading = ref(false), logError = ref(null), logPage = ref(1), logPageSize = ref(20), logTotal = ref(0)
const alertDays = ref(30), alertScope = ref('mine'), alertSource = ref(null), alertCategory = ref(null), alertSeverity = ref(null), alerts = ref([]), alertStats = ref({}), alertLoading = ref(false), alertError = ref(null), alertPage = ref(1), alertPageSize = ref(20), alertTotal = ref(0)

export function useSystemActivity({ client, message, isAdmin }) {
  const fetchLogData = async () => {
    logLoading.value = true
    logError.value = null
    try {
      const params = { days: logDays.value, page: logPage.value, page_size: logPageSize.value, scope: isAdmin.value ? logScope.value : 'mine' }
      if (logModule.value) params.module = logModule.value
      const [list, stats] = await Promise.all([client.listOplogs(params), client.oplogStats(logDays.value)])
      logs.value = list.data?.items || []; logTotal.value = list.data?.total || 0; logStats.value = stats.data || {}
    } catch (error) {
      logs.value = []; logTotal.value = 0; logError.value = normalizeError(error)
      if (error.response?.status === 403) { message.warning(error.response?.data?.detail || '当前账号无权查看该范围日志'); if (logModule.value === 'system') logModule.value = null; logScope.value = 'mine' }
    } finally { logLoading.value = false }
  }
  const fetchAlerts = async () => {
    alertLoading.value = true
    alertError.value = null
    try {
      const params = { days: alertDays.value, page: alertPage.value, page_size: alertPageSize.value, scope: isAdmin.value ? alertScope.value : 'mine' }
      if (alertSource.value) params.source = alertSource.value
      if (alertCategory.value) params.category = alertCategory.value
      if (alertSeverity.value) params.severity = alertSeverity.value
      const [list, stats] = await Promise.all([client.listAlerts(params), client.alertStats(params)])
      alerts.value = list.data?.items || []; alertTotal.value = list.data?.total || 0; alertStats.value = stats.data || {}
    } catch (error) {
      alerts.value = []; alertStats.value = {}; alertTotal.value = 0; alertError.value = normalizeError(error)
      if (error.response?.status === 403) { message.warning(error.response?.data?.detail || '当前账号无权查看该范围告警'); alertScope.value = 'mine' }
    } finally { alertLoading.value = false }
  }
  const resetLogPagination = async () => { logPage.value = 1; await fetchLogData() }
  const handleLogPageChange = async (page) => { logPage.value = page; await fetchLogData() }
  const resetAlertPagination = async () => { alertPage.value = 1; await fetchAlerts() }
  const handleAlertPageChange = async (page) => { alertPage.value = page; await fetchAlerts() }
  return { logDays, logModule, logScope, logs, logStats, logLoading, logError, logPage, logPageSize, logTotal, alertDays, alertScope, alertSource, alertCategory, alertSeverity, alerts, alertStats, alertLoading, alertError, alertPage, alertPageSize, alertTotal, fetchLogData, fetchAlerts, resetLogPagination, handleLogPageChange, resetAlertPagination, handleAlertPageChange }
}
