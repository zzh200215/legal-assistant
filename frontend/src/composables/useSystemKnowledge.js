import { ref } from 'vue'
import { normalizeError } from '../api/errors.js'

export function useSystemKnowledge({ client, message }) {
  const knowledgeLoading = ref(false)
  const knowledgeBases = ref([])
  const knowledgeDocuments = ref([])
  const sensitivityLoading = ref(false)
  const sensitivityError = ref(null)
  const sensitiveDocuments = ref([])

  const fetchKnowledgeData = async () => {
    knowledgeLoading.value = true
    try {
      const [kbRes, docRes] = await Promise.all([client.listKnowledgeBases(), client.listDocuments({ page: 1, page_size: 20 })])
      knowledgeBases.value = kbRes.data || []; knowledgeDocuments.value = docRes.data?.items || []
    } catch (error) {
      knowledgeBases.value = []; knowledgeDocuments.value = []
      message.error(error.response?.data?.detail || '获取知识库数据失败')
    } finally { knowledgeLoading.value = false }
  }

  const fetchSensitiveDocuments = async () => {
    sensitivityLoading.value = true
    sensitivityError.value = null
    try {
      const [{ data }, { data: restricted }] = await Promise.all([
        client.listDocuments({ page: 1, page_size: 50, sensitivity_level: 'confidential' }),
        client.listDocuments({ page: 1, page_size: 50, sensitivity_level: 'restricted' }),
      ])
      sensitiveDocuments.value = [...(data?.items || []), ...(restricted?.items || [])]
    } catch (error) {
      sensitiveDocuments.value = []
      sensitivityError.value = normalizeError(error)
    } finally { sensitivityLoading.value = false }
  }

  return { knowledgeLoading, knowledgeBases, knowledgeDocuments, sensitivityLoading, sensitivityError, sensitiveDocuments, fetchKnowledgeData, fetchSensitiveDocuments }
}
