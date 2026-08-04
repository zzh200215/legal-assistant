import { ref } from 'vue'

const emptyOrganization = () => ({ name: '', code: '', description: '' })
const emptyDepartment = () => ({ organization_id: null, name: '', code: '', description: '' })
const emptyAssignment = () => ({ user_id: null, organization_id: null, department_id: null, job_title: '' })

export function useSystemOrganization({ client, message }) {
  const orgLoading = ref(false)
  const organizations = ref([])
  const departments = ref([])
  const users = ref([])
  const newOrg = ref(emptyOrganization())
  const newDepartment = ref(emptyDepartment())
  const userAssignForm = ref(emptyAssignment())

  const fetchOrgData = async () => {
    orgLoading.value = true
    try {
      const [orgRes, deptRes, userRes] = await Promise.all([client.listOrganizations(), client.listDepartments(), client.listUsers()])
      organizations.value = orgRes.data || []; departments.value = deptRes.data || []; users.value = userRes.data || []
    } catch (error) {
      organizations.value = []; departments.value = []; users.value = []
      message.error(error.response?.data?.detail || '获取组织架构失败')
    } finally { orgLoading.value = false }
  }

  const createOrganization = async () => {
    if (!newOrg.value.name || !newOrg.value.code) return
    orgLoading.value = true
    try { await client.createOrganization(newOrg.value); newOrg.value = emptyOrganization(); message.success('组织已创建'); await fetchOrgData() }
    catch (error) { message.error(error.response?.data?.detail || '组织创建失败') } finally { orgLoading.value = false }
  }

  const createDepartment = async () => {
    if (!newDepartment.value.organization_id || !newDepartment.value.name || !newDepartment.value.code) return
    orgLoading.value = true
    try { await client.createDepartment(newDepartment.value); newDepartment.value = emptyDepartment(); message.success('部门已创建'); await fetchOrgData() }
    catch (error) { message.error(error.response?.data?.detail || '部门创建失败') } finally { orgLoading.value = false }
  }

  const assignUserOrg = async () => {
    if (!userAssignForm.value.user_id) return
    orgLoading.value = true
    try {
      await client.assignUserOrg(userAssignForm.value.user_id, {
        organization_id: userAssignForm.value.organization_id,
        department_id: userAssignForm.value.department_id,
        job_title: userAssignForm.value.job_title || null,
      })
      message.success('用户归属已更新'); await fetchOrgData()
    } catch (error) { message.error(error.response?.data?.detail || '用户归属更新失败') } finally { orgLoading.value = false }
  }

  return { orgLoading, organizations, departments, users, newOrg, newDepartment, userAssignForm, fetchOrgData, createOrganization, createDepartment, assignUserOrg }
}
