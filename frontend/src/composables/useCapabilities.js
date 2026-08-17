import { computed } from 'vue'
import { useAuthStore } from '../stores/auth'

// 统一权限判断入口：页面/组件/路由守卫只允许使用 can/canAny/canAll，
// 禁止散落 role 字符串判断。权限未知（auth 未就绪）默认不放行。
export function useCapabilities() {
  const auth = useAuthStore()

  const capabilities = computed(() => auth.capabilities)
  const ready = computed(() => auth.ready)

  const can = (cap) => ready.value && capabilities.value.includes(cap)
  const canAny = (caps) => caps.some((c) => can(c))
  const canAll = (caps) => caps.every((c) => can(c))

  return {
    capabilities,
    ready,
    can,
    canAny,
    canAll,
  }
}
