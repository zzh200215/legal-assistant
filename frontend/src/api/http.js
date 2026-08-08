import axios from 'axios'
import router from '../router'

const http = axios.create({ baseURL: '/api' })

function unwrapPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    return payload
  }
  if (Object.prototype.hasOwnProperty.call(payload, 'success')) {
    return payload.data
  }
  return payload
}

function getErrorMessage(payload) {
  if (!payload || typeof payload !== 'object') {
    return ''
  }
  return payload.message || payload.detail || payload.error?.detail || ''
}

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let redirectPromise = null
http.interceptors.response.use(
  (res) => {
    res.rawData = res.data
    res.data = unwrapPayload(res.data)
    return res
  },
  (err) => {
    const status = err.response?.status
    const isPublicPortalRequest = err.config?.url?.startsWith('/legal/portal/')
    const isBackgroundNotification = err.config?.url?.startsWith('/developer/notifications')
    const onPublicRoute = router.currentRoute.value.meta?.public === true

    // 公开路由（如客户门户 /portal/c/:token）上的 401 不应强制跳登录——组件自行处理错误态
    // 通知铃铛属后台轮询：401 不应触发强制登出（会话过期由 getMe 等关键请求负责兜底）
    if (status === 401 && !isPublicPortalRequest && !isBackgroundNotification && !onPublicRoute && !redirectPromise) {
      localStorage.removeItem('token')
      redirectPromise = router.push('/login').finally(() => { redirectPromise = null })
    }

    if (err.response?.data) {
      err.response.rawData = err.response.data
      const detail = getErrorMessage(err.response.data)
      err.response.data = { ...err.response.data, detail }

      // 422 validation errors: collect field messages for clearer display
      if (status === 422 && err.response.rawData?.detail && Array.isArray(err.response.rawData.detail)) {
        const fieldErrors = err.response.rawData.detail
          .map(e => `${e.loc?.slice(-1)[0] || ''}: ${e.msg}`)
          .join('; ')
        err.response.data.detail = fieldErrors || '参数校验失败'
      }

      // 429 rate limit
      if (status === 429) {
        err.response.data.detail = err.response.data.detail || '操作过于频繁，请稍后再试'
      }
    }

    return Promise.reject(err)
  }
)

export default http
