import router from '../router'
import { createRefreshCoordinator } from './refresh'

// 统一 API client（原生 fetch 实现，不依赖 axios）：
// baseURL / 认证 / 超时 / 401 单飞刷新 / envelope 解包 / 错误转换。
// 401 处理（集中）：并发 401 共享同一个 refresh 流程（单飞）；刷新失败按现有流程登出；
// 公开门户路由与通知轮询豁免（组件自行处理错误态）。

const BASE_URL = '/api'
const TIMEOUT_MS = 60_000

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

function buildUrl(url, params) {
  let target = url.startsWith('http://') || url.startsWith('https://') ? url : `${BASE_URL}${url}`
  if (!params) return target
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return
    if (Array.isArray(value)) {
      value.forEach((item) => {
        if (item !== undefined && item !== null) search.append(key, String(item))
      })
      return
    }
    search.append(key, String(value))
  })
  const query = search.toString()
  if (!query) return target
  return `${target}${target.includes('?') ? '&' : '?'}${query}`
}

function toHeaderObject(headers) {
  const result = {}
  headers.forEach((value, key) => {
    result[key.toLowerCase()] = value
  })
  return result
}

function buildInit(config) {
  const headers = { ...(config.headers || {}) }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const data = config.data
  let body
  if (data !== undefined && data !== null) {
    if (typeof FormData !== 'undefined' && data instanceof FormData) {
      body = data
    } else if (typeof Blob !== 'undefined' && data instanceof Blob) {
      body = data
    } else if (typeof data === 'string') {
      body = data
    } else {
      body = JSON.stringify(data)
      if (!headers['Content-Type']) headers['Content-Type'] = 'application/json'
    }
  }

  const init = {
    method: String(config.method || 'get').toUpperCase(),
    headers,
    body,
  }
  if (config.credentials) init.credentials = config.credentials
  return init
}

function normalizeErrorPayload(payload, status) {
  if (!payload || typeof payload !== 'object') return payload
  const next = { ...payload, detail: getErrorMessage(payload) }
  if (status === 422 && Array.isArray(payload.detail)) {
    next.detail =
      payload.detail
        .map((item) => `${item.loc?.slice(-1)[0] || ''}: ${item.msg}`)
        .join('; ') || '参数校验失败'
  }
  if (status === 429) {
    next.detail = next.detail || '操作过于频繁，请稍后再试'
  }
  return next
}

async function parseBody(response, responseType) {
  if (response.status === 204 || response.status === 205) return null
  const contentType = response.headers.get('content-type') || ''
  if (response.ok && responseType === 'blob') {
    return response.blob()
  }
  if (contentType.includes('json')) {
    return response.json().catch(() => null)
  }
  const text = await response.text()
  return text || null
}

function makeError(message, config, response) {
  const error = new Error(message)
  error.config = config
  if (response) error.response = response
  return error
}

async function request(config) {
  const url = buildUrl(config.url, config.params)
  const init = buildInit(config)
  const externalSignal = config.signal
  const controller = new AbortController()
  let timedOut = false
  const onAbort = () => controller.abort()
  if (externalSignal) {
    if (externalSignal.aborted) controller.abort()
    else externalSignal.addEventListener('abort', onAbort, { once: true })
  }
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, TIMEOUT_MS)
  init.signal = controller.signal
  const cleanup = () => {
    clearTimeout(timer)
    if (externalSignal) externalSignal.removeEventListener('abort', onAbort)
  }

  let response
  try {
    response = await fetch(url, init)
  } catch (err) {
    cleanup()
    const canceled = timedOut || err?.name === 'AbortError'
    const message = timedOut
      ? `timeout of ${TIMEOUT_MS}ms exceeded`
      : err?.message || 'Network Error'
    const error = makeError(message, config)
    error.code = timedOut ? 'ECONNABORTED' : canceled ? 'ERR_CANCELED' : 'ERR_NETWORK'
    throw error
  }

  const headers = toHeaderObject(response.headers)
  let rawData
  try {
    rawData = await parseBody(response, config.responseType)
  } finally {
    cleanup()
  }
  if (response.ok) {
    return {
      data: unwrapPayload(rawData),
      rawData,
      status: response.status,
      statusText: response.statusText,
      headers,
      config,
    }
  }

  const data = normalizeErrorPayload(rawData, response.status)
  const err = makeError(getErrorMessage(data) || response.statusText, config, {
    status: response.status,
    statusText: response.statusText,
    data,
    rawData,
    headers,
  })

  const requestUrl = config.url || ''
  const isPublicPortalRequest = requestUrl.startsWith('/legal/portal/')
  const isBackgroundNotification = requestUrl.startsWith('/developer/notifications')
  const onPublicRoute = router.currentRoute.value.meta?.public === true

  // 401 单飞刷新：仅对需要登录态的业务请求生效；刷新失败/重放后仍 401 → 登出
  if (
    response.status === 401 &&
    !isPublicPortalRequest &&
    !isBackgroundNotification &&
    !onPublicRoute &&
    !requestUrl.startsWith('/auth/refresh')
  ) {
    if (!config._retried) {
      const recovered = await refreshCoordinator.recover()
      if (recovered) {
        return request({ ...config, _retried: true })
      }
    } else {
      redirectToLogin()
    }
  }

  throw err
}

// 单飞刷新放在 http 定义之后：refreshFn 内部调用 http.post 时不会形成循环引用。
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

function http(config) {
  return request(config)
}

http.get = (url, config = {}) => request({ ...config, url, method: 'get' })
http.post = (url, data, config = {}) => request({ ...config, url, method: 'post', data })
http.put = (url, data, config = {}) => request({ ...config, url, method: 'put', data })
http.patch = (url, data, config = {}) => request({ ...config, url, method: 'patch', data })
http.delete = (url, config = {}) => request({ ...config, url, method: 'delete' })

export default http
