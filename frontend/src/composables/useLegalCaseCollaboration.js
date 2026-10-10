import { ref } from 'vue'

// ElMessageBox confirm/prompt 的取消 reject 值；用户主动取消不算错误，不应弹提示
const isUserCancel = (error) => error === 'cancel' || error === 'close' || error?.message === 'cancel'
const errorText = (error, fallback) => error?.response?.data?.detail || fallback

export function useLegalCaseCollaboration({ client, message, confirm, organizationId, caseId }) {
  const portalLinks = ref([])
  const showPortalDialog = ref(false)
  const portalCreating = ref(false)
  const portalForm = ref({ client_email: '', expires_days: 30, require_email_verification: true, aggregate_case: false })
  const progressUpdates = ref([])
  const progressForm = ref({ title: '', body: '', next_steps: '', visibility: 'internal' })
  const progressLoading = ref(false)
  const caseMembers = ref([])

  const hasCase = () => Boolean(caseId.value)
  const loadPortalLinks = async () => {
    if (!hasCase()) return
    try { portalLinks.value = (await client.listPortalLinks(organizationId.value, caseId.value)).data } catch (error) { console.error('[portal] 门户链接列表加载失败', error) }
  }
  const createPortalLink = async () => {
    portalCreating.value = true
    try {
      const { data } = await client.createPortalLink(organizationId.value, caseId.value, portalForm.value)
      message.success(`门户链接已创建，令牌前缀：${data.token_prefix}`)
      showPortalDialog.value = false
      portalForm.value = { client_email: '', expires_days: 30, require_email_verification: true, aggregate_case: false }
      await loadPortalLinks()
    } catch (error) { message.error(errorText(error, '创建失败')) } finally { portalCreating.value = false }
  }
  const revokePortalLink = async (row) => {
    try {
      await confirm('确认撤销该门户链接？撤销后客户将无法访问。', '撤销确认', { type: 'warning' })
      await client.revokePortalLink(row.id)
      message.success('已撤销')
      await loadPortalLinks()
    } catch (error) {
      if (isUserCancel(error)) return
      message.error(errorText(error, '撤销失败，请重试'))
    }
  }
  const loadProgressUpdates = async () => {
    if (!hasCase()) return
    try { const { data } = await client.listProgressUpdates(organizationId.value, caseId.value); progressUpdates.value = data.items || data } catch (error) { console.error('[portal] 进度更新列表加载失败', error) }
  }
  const submitProgressUpdate = async () => {
    if (!progressForm.value.title.trim() || !progressForm.value.body.trim()) return message.warning('标题和内容为必填')
    progressLoading.value = true
    try {
      await client.createProgressUpdate(organizationId.value, caseId.value, progressForm.value)
      message.success('进度更新已创建')
      progressForm.value = { title: '', body: '', next_steps: '', visibility: 'internal' }
      await loadProgressUpdates()
    } catch (error) { message.error(errorText(error, '创建失败')) } finally { progressLoading.value = false }
  }
  const publishProgress = async (row) => {
    try { await confirm('确认发布该进度更新？客户可见更新将通知客户。', '发布确认'); await client.publishProgressUpdate(row.id); message.success('已发布'); await loadProgressUpdates() } catch (error) {
      if (isUserCancel(error)) return
      message.error(errorText(error, '发布失败，请重试'))
    }
  }
  const withdrawProgress = async (row) => {
    try { await confirm('确认撤回该进度更新？', '撤回确认', { type: 'warning' }); await client.withdrawProgressUpdate(row.id); message.success('已撤回'); await loadProgressUpdates() } catch (error) {
      if (isUserCancel(error)) return
      message.error(errorText(error, '撤回失败，请重试'))
    }
  }
  const loadCaseMembers = async () => {
    if (!organizationId.value || !hasCase()) return
    try { caseMembers.value = (await client.listCaseMembers(organizationId.value, caseId.value)).data } catch (error) { console.error('[portal] 案件成员列表加载失败', error) }
  }
  const removeCaseMember = async (row) => {
    try { await confirm('确认移除该成员？', '移除确认', { type: 'warning' }); await client.patchCaseMember(row.id, { revoke: true }); message.success('已移除'); await loadCaseMembers() } catch (error) {
      if (isUserCancel(error)) return
      message.error(errorText(error, '移除失败，请重试'))
    }
  }

  return {
    portalLinks, showPortalDialog, portalCreating, portalForm, progressUpdates, progressForm, progressLoading, caseMembers,
    loadPortalLinks, createPortalLink, revokePortalLink, loadProgressUpdates, submitProgressUpdate,
    publishProgress, withdrawProgress, loadCaseMembers, removeCaseMember,
  }
}
