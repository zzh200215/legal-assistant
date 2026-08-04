import { ref } from 'vue'

const emptySource = () => ({
  title: '', source_type: 'statute', citation: '', jurisdiction: '中国大陆',
  version: 'v1', status: 'active', content: '', document_number: '',
  promulgator: '', full_text: '', law_areas: [], keywordsInput: '', keywords: [],
})

export function useLegalSources({ client, message, confirm }) {
  const legalSources = ref([])
  const importLoading = ref(false)
  const importResult = ref(null)
  const retrievalQuestion = ref('')
  const retrievalLoading = ref(false)
  const retrievalResult = ref(null)
  const sourceDialogVisible = ref(false)
  const editingSource = ref(null)
  const sourceSaving = ref(false)
  const sourceForm = ref(emptySource())

  const loadLegalSources = async () => {
    try {
      const { data } = await client.listLegalSources()
      legalSources.value = data
    } catch {}
  }

  const handleSourceImport = async (file) => {
    importLoading.value = true
    importResult.value = null
    try {
      const { data } = await client.importLegalSources(file)
      importResult.value = data
      message.success(`导入成功 ${data.imported} 条，跳过 ${data.skipped} 条`)
      await loadLegalSources()
    } catch (error) {
      message.error(error.response?.data?.detail || '导入失败')
    } finally {
      importLoading.value = false
    }
    return false
  }

  const syncKeywords = () => {
    sourceForm.value.keywords = (sourceForm.value.keywordsInput || '')
      .split(/[,，、]/).map((item) => item.trim()).filter(Boolean)
  }

  const openSourceDialog = (row) => {
    editingSource.value = row || null
    sourceForm.value = row ? {
      ...emptySource(), ...row,
      keywordsInput: (row.keywords || []).join('、'), keywords: row.keywords || [],
    } : emptySource()
    sourceDialogVisible.value = true
  }

  const saveSource = async () => {
    if (!sourceForm.value.title.trim() || !sourceForm.value.content.trim()) {
      return message.warning('标题和内容为必填项')
    }
    sourceSaving.value = true
    try {
      if (editingSource.value) {
        await client.updateSource(editingSource.value.id, sourceForm.value)
        message.success('法源已更新')
      } else {
        await client.createSource(sourceForm.value)
        message.success('法源已创建')
      }
      sourceDialogVisible.value = false
      await loadLegalSources()
    } catch (error) {
      message.error(error.response?.data?.detail || '保存失败')
    } finally {
      sourceSaving.value = false
    }
  }

  const deleteSourceHandler = async (row) => {
    try {
      await confirm(`确认删除「${row.title}」？删除后不可恢复。`, '确认删除', {
        confirmButtonText: '确认删除', cancelButtonText: '取消', type: 'warning',
      })
      await client.deleteSource(row.id)
      message.success('法源已删除')
      await loadLegalSources()
    } catch {}
  }

  const updateSourceStatus = async (row) => {
    try {
      await client.updateSourceStatus(row.id, row.status)
      message.success('状态更新成功')
    } catch (error) {
      message.error(error.response?.data?.detail || '状态更新失败')
      await loadLegalSources()
    }
  }

  const submitRetrievalTest = async () => {
    if (!retrievalQuestion.value.trim()) return message.warning('请输入测试问题')
    retrievalLoading.value = true
    try {
      const { data } = await client.testRetrieval(retrievalQuestion.value)
      retrievalResult.value = data
    } catch (error) {
      message.error(error.response?.data?.detail || '检索测试失败')
    } finally {
      retrievalLoading.value = false
    }
  }

  return {
    legalSources, importLoading, importResult, retrievalQuestion, retrievalLoading, retrievalResult,
    sourceDialogVisible, editingSource, sourceSaving, sourceForm,
    loadLegalSources, handleSourceImport, syncKeywords, openSourceDialog, saveSource,
    deleteSourceHandler, updateSourceStatus, submitRetrievalTest,
  }
}
