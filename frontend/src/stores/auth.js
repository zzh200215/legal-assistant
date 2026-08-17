import { defineStore } from 'pinia'
import api from '../api'
import { capabilitiesForRole } from '../auth/capabilities'

// 认证/当前用户共享状态：集中承载 getMe() 结果，避免各视图各自拉取并持有 currentUser 副本。
// 视图只读 user/isAdmin/capabilities；登录态变化由 Login.vue / App.vue 经 setUser/clear 同步。
// loadMe 用模块级 Promise 记忆化，多个子组件并发 await 只触发一次 getMe 请求。
// ready：/auth/me 是否已解析（成功或失败都置 true）。能力未就绪前 can() 一律返回 false（默认不放行）。
let loadPromise = null

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    ready: false,
  }),
  getters: {
    currentUser: (state) => state.user,
    isAdmin: (state) => state.user?.role === 'admin',
    capabilities: (state) => (state.user ? capabilitiesForRole(state.user.role) : []),
  },
  actions: {
    async loadMe() {
      if (!loadPromise) {
        loadPromise = (async () => {
          try {
            const { data } = await api.getMe()
            this.user = data
            return data
          } catch {
            this.user = null
            return null
          } finally {
            this.ready = true
          }
        })()
      }
      return loadPromise
    },
    setUser(user) {
      this.user = user
      this.ready = true
      loadPromise = Promise.resolve(user)
    },
    clear() {
      this.user = null
      this.ready = false
      loadPromise = null
    },
  },
})
