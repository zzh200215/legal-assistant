import { ref } from 'vue'

export function useLegalCaseCollaboration({ client, message, confirm, organizationId, caseId }) {
  const portalLinks = ref([])
  const showPortalDialog = ref(false)
  const portalCreating = ref(false)
  const portalForm = ref({ client_email: '', expires_days: 7, require_email_verification: true })
  const progressUpdates = ref([])
  const progressForm = ref({ title: '', body: '', next_steps: '', visibility: 'internal' })
  const progressLoading = ref(false)
  const caseMembers = ref([])

  const hasCase = () => Boolean(caseId.value)
  const loadPortalLinks = async () => {
    if (!hasCase()) return
    try { portalLinks.value = (await client.listPortalLinks(organizationId.value, caseId.value)).data } catch {}
  }
  const createPortalLink = async () => {
    portalCreating.value = true
    try {
      const { data } = await client.createPortalLink(organizationId.value, caseId.value, portalForm.value)
      message.success(`门户链接已创建，令牌前缀：${data.token_prefix}`)
      showPortalDialog.value = false
      portalForm.value = { client_email: '', expires_days: 7, require_email_verification: true }
      await loadPortalLinks()
    } catch (error) { message.error(error.response?.data?.detail || '创建失败') } finally { portalCreating.value = false }
  }
  const revokePortalLink = async (row) => {
    try {
      await confirm('确认撤销该门户链接？撤销后客户将无法访问。', '撤销确认', { type: 'warning' })
      await client.revokePortalLink(row.id)
      message.success('已撤销')
      await loadPortalLinks()
    } catch {}
  }
  const loadProgressUpdates = async () => {
    if (!hasCase()) return
    try { const { data } = await client.listProgressUpdates(organizationId.value, caseId.value); progressUpdates.value = data.items || data } catch {}
  }
  const submitProgressUpdate = async () => {
    if (!progressForm.value.title.trim() || !progressForm.value.body.trim()) return message.warning('标题和内容为必填')
    progressLoading.value = true
    try {
      await client.createProgressUpdate(organizationId.value, caseId.value, progressForm.value)
      message.success('进度更新已创建')
      progressForm.value = { title: '', body: '', next_steps: '', visibility: 'internal' }
      await loadProgressUpdates()
    } catch (error) { message.error(error.response?.data?.detail || '创建失败') } finally { progressLoading.value = false }
  }
  const publishProgress = async (row) => {
    try { await confirm('确认发布该进度更新？客户可见更新将通知客户。', '发布确认'); await client.publishProgressUpdate(row.id); message.success('已发布'); await loadProgressUpdates() } catch {}
  }
  const withdrawProgress = async (row) => {
    try { await confirm('确认撤回该进度更新？', '撤回确认', { type: 'warning' }); await client.withdrawProgressUpdate(row.id); message.success('已撤回'); await loadProgressUpdates() } catch {}
  }
  const loadCaseMembers = async () => {
    if (!organizationId.value || !hasCase()) return
    try { caseMembers.value = (await client.listCaseMembers(organizationId.value, caseId.value)).data } catch {}
  }
  const removeCaseMember = async (row) => {
    try { await confirm('确认移除该成员？', '移除确认', { type: 'warning' }); await client.patchCaseMember(row.id, { revoke: true }); message.success('已移除'); await loadCaseMembers() } catch {}
  }

  return {
    portalLinks, showPortalDialog, portalCreating, portalForm, progressUpdates, progressForm, progressLoading, caseMembers,
    loadPortalLinks, createPortalLink, revokePortalLink, loadProgressUpdates, submitProgressUpdate,
    publishProgress, withdrawProgress, loadCaseMembers, removeCaseMember,
  }
}
