import { computed, ref } from 'vue'
import { useQuery } from '../query/useQuery.js'
import { useMutation } from '../query/useMutation.js'
import { qk, qkPrefix } from '../query/keys'

// 法律咨询 tab 领域模块（查询层 + 幂等写）：
// 列表走统一查询层（同 key 去重/缓存/取消），提交/追问经 useMutation（Idempotency-Key 防连点重复）。

export function useLegalConsultations({ client, message, confirm, onReviewSubmitted, caseId }) {
  const consultForm = ref({ question: '' })
  const consultLoading = ref(false)
  const consultResult = ref(null)
  const consultations = ref([])
  const followupQuestion = ref('')
  const followupLoading = ref(false)

  const consultationsQuery = useQuery({
    key: () => qk.legal.consultations(caseId?.value || null),
    fetcher: () => client.listLegalConsultations({ params: caseId?.value ? { case_id: caseId.value } : undefined }),
    staleTime: 30 * 1000,
  })

  const consultationsList = computed(() => {
    const rows = consultationsQuery.data.value || []
    const activeCaseId = caseId?.value
    return activeCaseId ? rows.filter((row) => Number(row.case_id) === Number(activeCaseId)) : rows
  })

  const loadConsultations = async () => {
    await consultationsQuery.refetch()
    consultations.value = consultationsList.value
  }

  const submitMutation = useMutation({
    mutationFn: (payload, ctx) => client.createLegalConsultation(payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('legal', 'consultations')],
    onSuccess: (result) => {
      consultResult.value = result.data
      consultForm.value.question = ''
    },
    onError: (error) => {
      message.error(error.message || '咨询失败')
    },
  })

  const submitConsultation = async () => {
    if (!consultForm.value.question.trim()) return message.warning('请输入法律问题')
    consultLoading.value = true
    try {
      await submitMutation.mutate({
        body: {
          question: consultForm.value.question,
          case_id: caseId?.value || undefined,
        },
      })
    } finally {
      consultLoading.value = false
    }
  }

  const followupMutation = useMutation({
    mutationFn: (payload, ctx) => client.followupConsultation(payload.id, payload.question, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('legal', 'consultations')],
    onSuccess: (result) => {
      consultResult.value = result.data
      followupQuestion.value = ''
    },
    onError: (error) => {
      message.error(error.message || '追问失败')
    },
  })

  const submitFollowup = async () => {
    if (!followupQuestion.value.trim() || !consultResult.value) return
    followupLoading.value = true
    try {
      await followupMutation.mutate({ id: consultResult.value.id, question: followupQuestion.value })
    } finally {
      followupLoading.value = false
    }
  }

  // 咨询结果卡持久化（ux-audit D12 回归发现）：consultResult 原为会话本地 state，
  // 刷新/直达后结果与"提交律师审核"入口丢失；从历史详情恢复，保证送审链路在任何入口可达。
  const openDetailLoading = ref(false)
  const openConsultationDetail = async (id) => {
    if (!id || openDetailLoading.value) return
    openDetailLoading.value = true
    try {
      const { data } = await client.getLegalConsultation(id)
      consultResult.value = data
    } catch (error) {
      message.error(error.response?.data?.detail || error.message || '咨询结果加载失败')
    } finally {
      openDetailLoading.value = false
    }
  }

  const submitReviewMutation = useMutation({
    mutationFn: (payload, ctx) => client.submitLegalReviewAction(payload.type, payload.id, payload.body, { idempotencyKey: ctx.idempotencyKey }),
    invalidate: [qkPrefix('legal', 'consultations')],
    onSuccess: () => {
      consultResult.value.status = 'needs_lawyer_review'
      message.success('已提交律师审核队列')
    },
  })

  const submitConsultForReview = async () => {
    if (!consultResult.value) return
    try {
      await confirm(
        '确认将此咨询提交律师审核？提交后将进入审核队列，由审核律师进行复核。',
        '提交律师审核',
        { confirmButtonText: '确认提交', cancelButtonText: '取消', type: 'info' },
      )
      await submitReviewMutation.mutate({
        type: 'consultation',
        id: consultResult.value.id,
        body: { action: 'submit_review', note: '用户提交审核' },
      })
      await loadConsultations()
      await onReviewSubmitted()
    } catch {
      // 用户取消确认框：静默
    }
  }

  return {
    consultForm,
    consultLoading,
    consultResult,
    consultations: consultationsList,
    followupQuestion,
    followupLoading,
    openDetailLoading,
    openConsultationDetail,
    loadConsultations,
    submitConsultation,
    submitFollowup,
    submitConsultForReview,
  }
}
