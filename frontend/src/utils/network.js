import { ref } from 'vue'

// 全局网络状态单例：online/offline 监听，供查询层、轮询、WS 重连统一消费。
// 与项目既有「模块级单例」模式一致（useDocuments / useSystemTaskMonitor）。

const online = ref(
  typeof navigator !== 'undefined' && typeof navigator.onLine === 'boolean' ? navigator.onLine : true
)
const listeners = new Set()

function setOnline(value) {
  if (online.value === value) return
  online.value = value
  listeners.forEach((fn) => {
    try {
      fn(value)
    } catch {
      // 监听器异常不影响其他监听器
    }
  })
}

if (typeof window !== 'undefined') {
  window.addEventListener('online', () => setOnline(true))
  window.addEventListener('offline', () => setOnline(false))
}

/** 当前是否在线（响应式 ref） */
export function useOnline() {
  return online
}

/** 当前是否在线（同步读取） */
export function isOnline() {
  return online.value
}

/** 订阅网络状态变化，返回取消订阅函数 */
export function onNetworkChange(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}
