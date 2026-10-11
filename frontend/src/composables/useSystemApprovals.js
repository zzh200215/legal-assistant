import { computed, ref } from 'vue'
import { normalizeError } from '../api/errors.js'

// 审批台为模块级单例：顶部命令条展示「待审批」数，审批台 tab 展示完整列表，
// 二者共享同一份 approvals 状态，避免拆分后顶部统计与列表割裂。
const approvalsLoading = ref(false)
const approvalsError = ref(null)
const approvals = ref([])
const approvalStats = computed(() => ({
  pending: approvals.value.filter((item) => item.status === 'pending').length,
  approved: approvals.value.filter((item) => item.status === 'approved').length,
  executed: approvals.value.filter((item) => item.status === 'executed').length,
}))

export function useSystemApprovals({ client }) {
  const fetchApprovalData = async () => {
    approvalsLoading.value = true
    approvalsError.value = null
    try {
      const { data } = await client.listApprovals()
      approvals.value = data || []
    } catch (error) {
      approvals.value = []
      approvalsError.value = normalizeError(error)
    } finally { approvalsLoading.value = false }
  }
  return { approvalsLoading, approvalsError, approvals, approvalStats, fetchApprovalData }
}
