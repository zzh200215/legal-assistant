import { ref } from 'vue'

/**
 * 工作区面包屑：外壳（App.vue）负责渲染，页面负责登记自己这一级的上下文。
 *
 * 存在的理由：方向 B 的顶栏面包屑要显示「首页 / 案件 / 张三劳动合同纠纷」，而案件标题只有
 * 页面自己知道（来自接口）。用模块级 ref 而不是 props/emit，是因为 router-view 之间传不了
 * props，而这个状态天然是「当前页面 → 外壳」的单向流。
 *
 * 约定：
 * - 页面在拿到数据后调用 setWorkspaceCrumbs([{ label, path? }, ...])，最后一项不带 path 表示当前页；
 * - 页面卸载时（onUnmounted）调用 clearWorkspaceCrumbs()，外壳也会在路由变化时清空，避免标题残留；
 * - 没有任何页面登记时，外壳回退到路由对应的栏目名。
 */
const crumbs = ref([])

export function setWorkspaceCrumbs(items) {
  crumbs.value = Array.isArray(items) ? items.filter((item) => item && item.label) : []
}

export function clearWorkspaceCrumbs() {
  crumbs.value = []
}

export function useWorkspaceCrumbs() {
  return crumbs
}
