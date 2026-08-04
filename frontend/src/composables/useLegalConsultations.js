import { ref } from 'vue'

// Owns the complete consultation workflow so the page only renders its state.
export function useLegalConsultations({ client, message, confirm, onReviewSubmitted, caseId }) {
  const consultForm = ref({ question: '' })
  const consultLoading = ref(false)
  const consultResult = ref(null)
  const consultations = ref([])
  const followupQuestion = ref('')
  const followupLoading = ref(false)

  const loadConsultations = async () => {
    try {
      const { data } = await client.listLegalConsultations()
      consultations.value = data
    } catch {}
  }

  const submitConsultation = async () => {
    if (!consultForm.value.question.trim()) return message.warning('请输入法律问题')
    consultLoading.value = true
    try {
      const { data } = await client.createLegalConsultation({
        question: consultForm.value.question,
        case_id: caseId?.value || undefined,
      })
      consultResult.value = data
      consultForm.value.question = ''
      await loadConsultations()
    } catch (error) {
      message.error(error.response?.data?.detail || '咨询失败')
    } finally {
      consultLoading.value = false
    }
  }

  const submitFollowup = async () => {
    if (!followupQuestion.value.trim() || !consultResult.value) return
    followupLoading.value = true
    try {
      const { data } = await client.followupConsultation(consultResult.value.id, followupQuestion.value)
      consultResult.value = data
      followupQuestion.value = ''
      await loadConsultations()
    } catch (error) {
      message.error(error.response?.data?.detail || '追问失败')
    } finally {
      followupLoading.value = false
    }
  }

  const submitConsultForReview = async () => {
    if (!consultResult.value) return
    try {
      await confirm(
        '确认将此咨询提交律师审核？提交后将进入审核队列，由审核律师进行复核。',
        '提交律师审核',
        { confirmButtonText: '确认提交', cancelButtonText: '取消', type: 'info' },
      )
      await client.submitLegalReviewAction('consultation', consultResult.value.id, {
        action: 'submit_review', note: '用户提交审核',
      })
      consultResult.value.status = 'needs_lawyer_review'
      message.success('已提交律师审核队列')
      await loadConsultations()
      await onReviewSubmitted()
    } catch {}
  }

  return {
    consultForm,
    consultLoading,
    consultResult,
    consultations,
    followupQuestion,
    followupLoading,
    loadConsultations,
    submitConsultation,
    submitFollowup,
    submitConsultForReview,
  }
}
