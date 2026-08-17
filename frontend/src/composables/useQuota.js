import { ref } from 'vue'
import { subscription as subscriptionApi } from '../api'

// 订阅配额提示：集中承载配额查询与「剩余额度」文案，供工作台多个 tab 复用。
// quotaSummary 为模块级单例，父视图与各 tab 共享同一份配额状态。
const quotaSummary = ref(null)

export function useQuota() {
  const loadQuota = async () => {
    try {
      const { data } = await subscriptionApi.myQuota()
      quotaSummary.value = data
    } catch {
      // 配额接口不可用时静默降级，不阻塞主流程
      quotaSummary.value = null
    }
  }

  const typeLabel = (t) => ({ consultation: '咨询', review: '审查', draft: '文书' }[t] || t)

  const quotaHint = (type) => {
    const q = quotaSummary.value?.[type]
    if (!q || q.unlimited) return ''
    if (q.remaining <= 0) return `本月${typeLabel(type)}额度已用尽，升级解锁更多`
    return `本月${typeLabel(type)}剩余 ${q.remaining}/${q.quota}`
  }

  return { quotaSummary, loadQuota, quotaHint }
}
