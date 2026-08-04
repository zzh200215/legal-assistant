import { ref } from 'vue'

export function useContractComparison({ client, message }) {
  const compareForm = ref({ title_a: '', content_a: '', title_b: '', content_b: '' })
  const compareLoading = ref(false)
  const compareResult = ref(null)

  const submitCompare = async () => {
    if (!compareForm.value.content_a.trim() || !compareForm.value.content_b.trim()) {
      return message.warning('请输入两份合同内容')
    }
    compareLoading.value = true
    try {
      const { data } = await client.compareContracts(compareForm.value)
      compareResult.value = data
    } catch (error) {
      message.error(error.response?.data?.detail || '核对失败')
    } finally {
      compareLoading.value = false
    }
  }

  return { compareForm, compareLoading, compareResult, submitCompare }
}
