import { computed, onBeforeUnmount, ref, watch } from 'vue'

// LLM 生成的阶段性进度文案（ux-audit M-6）：loading 期间按时间轮换阶段提示，
// 缓解 20-30 秒等待中"不知道在干什么、还要多久"的焦虑。阶段按顺序只进不退。
export function useStagedProgress(active, stages, intervalMs = 8000) {
  const index = ref(0)
  let timer = null

  const stop = () => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  watch(
    active,
    (running) => {
      if (running) {
        index.value = 0
        stop()
        timer = setInterval(() => {
          index.value = Math.min(index.value + 1, stages.length - 1)
        }, intervalMs)
      } else {
        stop()
      }
    },
    { immediate: true },
  )
  onBeforeUnmount(stop)

  return computed(() => (active.value ? stages[index.value] : ''))
}
