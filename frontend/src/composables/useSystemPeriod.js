import { ref } from 'vue'

// 统计周期（天）模块级单例：Token 统计与工具健康 tab 共享同一周期选择，
// 避免把共享状态做成每次调用新建的 ref 导致跨组件割裂。
export const tokenDays = ref(30)
