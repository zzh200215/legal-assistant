import { ref } from 'vue'
import legalWorkspace from '../api/legalWorkspace'

// 法源核对（引用依据核对弹窗）共享状态与动作：供工作台多个 tab 复用。
// 状态为模块级单例：父视图的弹窗与各 tab 的 openSourceDetail 共享同一份状态。
// surface 标记「谁打开的」：legal = 工作台页面弹窗，copilot = 助手面板卡片内联展开；
// 两个出口同时挂载，靠该标记避免同一次点击在两侧重复展示。
const sourceDetailVisible = ref(false)
const sourceDetail = ref(null)
const sourceDetailArticles = ref([])
const sourceDetailLoading = ref(false)
const sourceDetailSurface = ref('legal')

export function useLegalSourceDetail() {
  const openSourceDetail = async (refItem, surface = 'legal') => {
    sourceDetailSurface.value = surface
    sourceDetail.value = refItem
    sourceDetailArticles.value = []
    sourceDetailVisible.value = true
    if (!refItem?.source_id) return
    sourceDetailLoading.value = true
    try {
      const { data } = await legalWorkspace.getSourceArticles(refItem.source_id)
      sourceDetailArticles.value = data || []
    } catch {
      sourceDetailArticles.value = []
    } finally {
      sourceDetailLoading.value = false
    }
  }

  const openRecommendedSource = async (recommended) => {
    if (!recommended?.source_id) return
    openSourceDetail({ ...recommended, source_id: recommended.source_id })
  }
  const verificationTagType = (v) => {
    if (!v || v.verified === false) return 'info'
    if (v.status === 'inactive') return 'danger'
    if (v.superseded || v.status === 'pending_update') return 'warning'
    if (v.current_effective) return 'success'
    return 'info'
  }

  return {
    sourceDetailVisible,
    sourceDetail,
    sourceDetailArticles,
    sourceDetailLoading,
    sourceDetailSurface,
    openSourceDetail,
    openRecommendedSource,
    verificationTagType,
  }
}
