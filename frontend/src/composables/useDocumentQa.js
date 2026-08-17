import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useMutation } from '../query/useMutation.js'
import { qkPrefix } from '../query/keys'
import api from '../api'

// 文档「问答 + 反馈」领域模块：提问（引用溯源）、正/负反馈闭环。
// 写操作经 useMutation（Idempotency-Key），成功后精准失效问答记录查询。

const emptyFeedback = () => ({ feedback_reason: '', feedback_note: '' })

const feedbackReasonOptions = [
  { label: '答案不准确', value: 'incorrect_answer' },
  { label: '引用不准确', value: 'wrong_citation' },
  { label: '信息不完整', value: 'incomplete_answer' },
  { label: '没有帮助', value: 'not_helpful' },
]

const feedbackValueText = (value) => ({
  positive: '正反馈',
  negative: '负反馈',
}[value] || value || '未反馈')

const feedbackTagType = (value) => ({
  positive: 'success',
  negative: 'danger',
}[value] || 'info')

export function useDocumentQa({ docId, onAsked }) {
  const qaQuestion = ref('')
  const qaResult = ref(null)
  const asking = ref(false)
  const submittingFeedback = ref(false)
  const negativeFeedbackVisible = ref(false)
  const feedbackForm = ref(emptyFeedback())

  const resetForNewDocument = () => {
    qaQuestion.value = ''
    qaResult.value = null
    negativeFeedbackVisible.value = false
    feedbackForm.value = emptyFeedback()
  }

  const askMutation = useMutation({
    mutationFn: (payload, ctx) => api.askDocument(payload.docId, payload.question, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('documents', 'qa-records')],
    onSuccess: (result) => {
      const data = result.data
      qaResult.value = {
        qa_record_id: data.qa_record_id,
        answer: data.answer || '',
        citations: data.citations || [],
        confidence: data.confidence || 0,
        can_answer: data.can_answer !== false,
        feedback_value: data.feedback_value || null,
        feedback_status: data.feedback_status || null,
      }
      negativeFeedbackVisible.value = false
      feedbackForm.value = emptyFeedback()
      qaQuestion.value = ''
      if (onAsked) onAsked()
    },
    onError: (error) => {
      qaResult.value = null
      ElMessage.error(error.message || '文档问答失败')
    },
  })

  const askDocumentQuestion = async () => {
    if (!docId.value || !qaQuestion.value.trim()) return
    asking.value = true
    try {
      await askMutation.mutate({ docId: docId.value, question: qaQuestion.value.trim() })
    } finally {
      asking.value = false
    }
  }

  const feedbackMutation = useMutation({
    mutationFn: (payload, ctx) => api.submitQaFeedback(payload.qaRecordId, payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('documents', 'qa-records')],
    onSuccess: (result) => {
      qaResult.value = {
        ...qaResult.value,
        feedback_value: result.data?.feedback_value,
        feedback_status: result.data?.feedback_status,
      }
      negativeFeedbackVisible.value = false
      feedbackForm.value = emptyFeedback()
      ElMessage.success('反馈已提交')
      if (onAsked) onAsked()
    },
    onError: (error) => {
      ElMessage.error(error.message || '提交反馈失败')
    },
  })

  const submitQaFeedback = async (payload) => {
    if (!qaResult.value?.qa_record_id) return
    submittingFeedback.value = true
    try {
      await feedbackMutation.mutate({ qaRecordId: qaResult.value.qa_record_id, body: payload })
    } finally {
      submittingFeedback.value = false
    }
  }

  const submitPositiveFeedback = () => submitQaFeedback({ feedback_value: 'positive' })
  const openNegativeFeedback = () => { negativeFeedbackVisible.value = true }
  const cancelNegativeFeedback = () => { negativeFeedbackVisible.value = false; feedbackForm.value = emptyFeedback() }
  const submitNegativeFeedback = () => submitQaFeedback({
    feedback_value: 'negative',
    feedback_reason: feedbackForm.value.feedback_reason || null,
    feedback_note: feedbackForm.value.feedback_note || null,
  })

  return {
    qaQuestion,
    qaResult,
    asking,
    submittingFeedback,
    negativeFeedbackVisible,
    feedbackForm,
    feedbackReasonOptions,
    feedbackValueText,
    feedbackTagType,
    resetForNewDocument,
    askDocumentQuestion,
    submitPositiveFeedback,
    openNegativeFeedback,
    cancelNegativeFeedback,
    submitNegativeFeedback,
  }
}
