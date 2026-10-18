// 助手面板（copilot）的全局开合状态。
//
// 产品口径是「入口收敛」：助手只有右侧面板这一个入口，`/chat` 降级为面板的「展开整页」深度视图。
// 因此导航栏开关、案件工作台「打开助手」、工作台「案件助手」快捷入口都改走这里，
// 而不是各自 router.push('/chat') —— 后者会离开当前页面，是收敛前的旧行为。
//
// 状态放在模块作用域（单例）：App.vue 挂载面板，业务页面只负责把它打开。
import { ref } from 'vue'

const open = ref(true)

/** 助手面板是否展开 */
export function useCopilotDock() {
  const openCopilot = () => {
    open.value = true
  }
  const closeCopilot = () => {
    open.value = false
  }
  const toggleCopilot = () => {
    open.value = !open.value
  }
  return { open, openCopilot, closeCopilot, toggleCopilot }
}
