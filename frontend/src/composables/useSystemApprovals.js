import { computed, ref } from 'vue'

export function useSystemApprovals({ client, message }) {
  const approvalsLoading = ref(false)
  const approvals = ref([])
  const approvalStats = computed(() => ({
    pending: approvals.value.filter((item) => item.status === 'pending').length,
    approved: approvals.value.filter((item) => item.status === 'approved').length,
    executed: approvals.value.filter((item) => item.status === 'executed').length,
  }))
  const fetchApprovalData = async () => {
    approvalsLoading.value = true
    try {
      const { data } = await client.listApprovals()
      approvals.value = data || []
    } catch (error) {
      approvals.value = []
      message.error(error.response?.data?.detail || '获取审批数据失败')
    } finally { approvalsLoading.value = false }
  }
  return { approvalsLoading, approvals, approvalStats, fetchApprovalData }
}
