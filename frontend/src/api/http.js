import axios from 'axios'
import router from '../router'
import { createRefreshCoordinator } from './refresh'

// 统一 API client：baseURL / 认证 / 超时 / 401 单飞刷新 / envelope 解包 / 错误转换。
// 401 处理（集中）：并发 401 共享同一个 refresh 流程（单飞）；刷新失败按现有流程登出；
// 公开门户路由与通知轮询豁免（组件自行处理错误态）。

const http = axios.create({ baseURL: '/api', timeout: 60000 })

const TOKEN_KEY = 'token'
const REFRESH_KEY = 'refresh_token'

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

// ── Token 存取（统一入口，禁止页面直接操作 localStorage token）──
export function getAccessToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setAccessToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY)
}

export function setRefreshToken(token) {
  if (token) localStorage.setItem(REFRESH_KEY, token)
  else localStorage.removeItem(REFRESH_KEY)
}

/** 写操作请求头：Idempotency-Key / If-Match（由 useMutation 统一传入） */
export function idempotencyHeaders(options = {}) {
  const headers = {}
  if (options.idempotencyKey) headers['Idempotency-Key'] = options.idempotencyKey
  if (options.ifMatch) headers['If-Match'] = options.ifMatch
  return headers
}

let redirectPromise = null

function redirectToLogin() {
  if (redirectPromise) return redirectPromise
  setAccessToken(null)
  setRefreshToken(null)
  redirectPromise = router.push('/login').finally(() => {
    redirectPromise = null
  })
  return redirectPromise
}

// 单飞刷新：并发 401 只触发一次 POST /auth/refresh（后端单次轮换），失败只登出一次
const refreshCoordinator = createRefreshCoordinator({
  refreshFn: async () => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) throw new Error('NO_REFRESH_TOKEN')
    const { data } = await http.post('/auth/refresh', { refresh_token: refreshToken })
    const access = data?.access_token
    if (!access) throw new Error('REFRESH_RESPONSE_MISSING_TOKEN')
    setAccessToken(access)
    if (data.refresh_token) setRefreshToken(data.refresh_token)
    return access
  },
  onExpired: () => redirectToLogin(),
})

http.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (res) => {
    res.rawData = res.data
    res.data = unwrapPayload(res.data)
    return res
  },
  async (err) => {
    const status = err.response?.status
    const config = err.config || {}
    const url = config.url || ''
    const isPublicPortalRequest = url.startsWith('/legal/portal/')
    const isBackgroundNotification = url.startsWith('/developer/notifications')
    const onPublicRoute = router.currentRoute.value.meta?.public === true

    // 401 单飞刷新：仅对需要登录态的业务请求生效；刷新失败/重放后仍 401 → 登出
    if (status === 401 && !isPublicPortalRequest && !isBackgroundNotification && !onPublicRoute && !url.startsWith('/auth/refresh')) {
      if (!config._retried) {
        const recovered = await refreshCoordinator.recover()
        if (recovered) {
          config._retried = true
          // 重放原请求：请求拦截器会自动带上新 access token
          return http(config)
        }
      } else {
        redirectToLogin()
      }
    }

    if (err.response?.data) {
      err.response.rawData = err.response.data
      const detail = getErrorMessage(err.response.data)
      err.response.data = { ...err.response.data, detail }

      // 422 validation errors: collect field messages for clearer display
      if (status === 422 && err.response.rawData?.detail && Array.isArray(err.response.rawData.detail)) {
        const fieldErrors = err.response.rawData.detail
          .map((e) => `${e.loc?.slice(-1)[0] || ''}: ${e.msg}`)
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
