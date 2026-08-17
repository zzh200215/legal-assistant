import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useMutation } from '../query/useMutation.js'
import { errorMessage } from '../api/errors'
import api from '../api'

// 文档「多文档对比 + 冲突建议 + 任务产出」领域模块：
// 对比、生成风险任务建议、确认风险任务、从文档提取待办创建任务，均为写操作（useMutation + 幂等键）。

export function useDocumentCompare({ docId }) {
  const compareSelection = ref([])
  const compareResult = ref(null)
  const compareLoading = ref(false)
  const conflictCases = ref([])
  const conflictSuggestionLoading = ref(false)
  const createdTasks = ref([])
  const creatingTasks = ref(false)

  const confirmedConflictCount = computed(() =>
    (compareResult.value?.comparison?.conflict_analysis?.conflicts || []).filter((item) => item.evidence_complete).length
  )

  const resetForNewDocument = () => {
    compareResult.value = null
    createdTasks.value = []
  }

  const compareMutation = useMutation({
    mutationFn: (payload, ctx) => api.compareDocuments(payload.documentIds, 500, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result) => {
      compareResult.value = result.data
      conflictCases.value = []
    },
    onError: (error) => {
      compareResult.value = null
      ElMessage.error(error.message || '文档对比失败')
    },
  })

  const runCompare = async () => {
    if (compareSelection.value.length < 2) {
      ElMessage.warning('至少选择两份文档')
      return
    }
    compareLoading.value = true
    try {
      await compareMutation.mutate({ documentIds: [...compareSelection.value] })
    } finally {
      compareLoading.value = false
    }
  }

  const suggestionMutation = useMutation({
    mutationFn: (payload, ctx) => api.createConflictSuggestions(payload.body, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result) => {
      conflictCases.value = result.data.items || []
      ElMessage.success(`已生成 ${conflictCases.value.length} 项待确认风险任务建议`)
    },
    onError: (error) => {
      ElMessage.error(error.message || '风险任务建议生成失败')
    },
  })

  const createConflictSuggestions = async () => {
    const conflicts = (compareResult.value?.comparison?.conflict_analysis?.conflicts || []).filter((item) => item.evidence_complete)
    if (!conflicts.length) return
    conflictSuggestionLoading.value = true
    try {
      await suggestionMutation.mutate({ body: { document_ids: compareSelection.value, conflicts } })
    } finally {
      conflictSuggestionLoading.value = false
    }
  }

  const confirmMutation = useMutation({
    mutationFn: (payload, ctx) => api.confirmConflictTask(payload.caseId, {}, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result, variables) => {
      const data = result.data
      const index = conflictCases.value.findIndex((caseItem) => caseItem.id === variables.caseId)
      if (index >= 0) conflictCases.value[index] = data.case
      ElMessage.success(`风险任务 #${data.task.id} 已创建`)
    },
    onError: (error) => {
      ElMessage.error(error.message || '风险任务创建失败')
    },
  })

  const confirmConflictTask = async (item) => {
    try {
      await ElMessageBox.confirm('将创建一条可追溯的内部风险任务，任务说明会包含双侧原文证据和定位信息。', '确认创建风险任务', { type: 'warning', confirmButtonText: '确认创建', cancelButtonText: '取消' })
      await confirmMutation.mutate({ caseId: item.id })
    } catch (e) {
      if (e !== 'cancel' && e !== 'close') ElMessage.error(errorMessage(e) || '风险任务创建失败')
    }
  }

  const createTasksMutation = useMutation({
    mutationFn: (payload, ctx) => api.createTasksFromDocument(payload.docId, { idempotencyKey: ctx.idempotencyKey }),
    onSuccess: (result) => {
      createdTasks.value = result.data.tasks || []
      ElMessage.success(`已创建 ${result.data.created_tasks || 0} 条任务`)
    },
    onError: (error) => {
      ElMessage.error(error.message || '创建任务失败')
    },
  })

  const createTasks = async () => {
    if (!docId.value) return
    creatingTasks.value = true
    try {
      await createTasksMutation.mutate({ docId: docId.value })
    } finally {
      creatingTasks.value = false
    }
  }

  return {
    compareSelection,
    compareResult,
    compareLoading,
    conflictCases,
    conflictSuggestionLoading,
    createdTasks,
    creatingTasks,
    confirmedConflictCount,
    resetForNewDocument,
    runCompare,
    createConflictSuggestions,
    confirmConflictTask,
    createTasks,
  }
}
