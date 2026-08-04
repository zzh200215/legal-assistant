import { ref } from 'vue'

const emptyFeedback = () => ({ feedback_reason: '', feedback_note: '' })

export function useDocumentQaFeedback({ client, message, documentId, refreshRecords }) {
  const qaQuestion = ref('')
  const qaResult = ref(null)
  const asking = ref(false)
  const submittingFeedback = ref(false)
  const negativeFeedbackVisible = ref(false)
  const feedbackForm = ref(emptyFeedback())
  const resetFeedbackForm = () => { feedbackForm.value = emptyFeedback() }
  const askDocumentQuestion = async () => {
    if (!documentId.value || !qaQuestion.value.trim()) return
    asking.value = true
    try {
      const { data } = await client.askDocument(documentId.value, qaQuestion.value.trim())
      qaResult.value = { qa_record_id: data.qa_record_id, answer: data.answer || '', citations: data.citations || [], confidence: data.confidence || 0, can_answer: data.can_answer !== false, feedback_value: data.feedback_value || null, feedback_status: data.feedback_status || null }
      negativeFeedbackVisible.value = false; resetFeedbackForm(); qaQuestion.value = ''; await refreshRecords()
    } catch (error) { qaResult.value = null; message.error(error.response?.data?.detail || '文档问答失败') } finally { asking.value = false }
  }
  const submitQaFeedback = async (payload) => {
    if (!qaResult.value?.qa_record_id) return
    submittingFeedback.value = true
    try {
      const { data } = await client.submitQaFeedback(qaResult.value.qa_record_id, payload)
      qaResult.value = { ...qaResult.value, feedback_value: data.feedback_value, feedback_status: data.feedback_status }
      negativeFeedbackVisible.value = false; resetFeedbackForm(); await refreshRecords(); message.success('反馈已提交')
    } catch (error) { message.error(error.response?.data?.detail || '提交反馈失败') } finally { submittingFeedback.value = false }
  }
  const submitPositiveFeedback = () => submitQaFeedback({ feedback_value: 'positive' })
  const openNegativeFeedback = () => { negativeFeedbackVisible.value = true }
  const cancelNegativeFeedback = () => { negativeFeedbackVisible.value = false; resetFeedbackForm() }
  const submitNegativeFeedback = () => submitQaFeedback({ feedback_value: 'negative', feedback_reason: feedbackForm.value.feedback_reason || null, feedback_note: feedbackForm.value.feedback_note || null })
  return { qaQuestion, qaResult, asking, submittingFeedback, negativeFeedbackVisible, feedbackForm, resetFeedbackForm, askDocumentQuestion, submitPositiveFeedback, openNegativeFeedback, cancelNegativeFeedback, submitNegativeFeedback }
}
