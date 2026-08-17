import { isOnline } from '../utils/network.js'

// 统一错误规范化：把 axios/业务/网络/取消错误映射为稳定的前端错误对象，
// 页面只消费 normalizeError 后的 { kind, code, message, detail, requestId, traceId }，
// 不再散落手写 error.response?.data?.detail 与裸异常文本。
// 错误码对照后端 x-error-codes 注册表（docs/openapi-snapshot.json 顶层 x-error-codes）。

export const ErrorKind = {
  NETWORK: 'network',
  TIMEOUT: 'timeout',
  OFFLINE: 'offline',
  CANCELLED: 'cancelled',
  UNAUTHORIZED: 'unauthorized',
  FORBIDDEN: 'forbidden',
  CONFLICT: 'conflict',
  VALIDATION: 'validation',
  RATE_LIMIT: 'rate_limit',
  BUSINESS: 'business',
  SERVER: 'server',
  UNKNOWN: 'unknown',
}

const CODE_TEXT = {
  VALIDATION_ERROR: '请求参数校验失败，请检查输入后重试',
  CONCURRENT_UPDATE_CONFLICT: '内容已被其他用户修改，请刷新后重试',
  IDEMPOTENCY_KEY_CONFLICT: '检测到重复提交，已忽略本次操作',
  IDEMPOTENCY_KEY_MISSING: '操作缺少幂等标识，请重试',
  ADMIN_REQUIRED: '需要管理员权限',
  USER_DISABLED: '账号已被禁用',
  INVALID_CREDENTIALS: '登录状态已失效，请重新登录',
  JOB_NOT_FOUND: '任务不存在或已过期',
  JOB_NOT_CANCELLABLE: '任务当前状态无法取消',
  DOCUMENT_NOT_FOUND: '文档不存在或无权访问',
  TASK_NOT_FOUND: '任务不存在',
  ORG_NOT_FOUND: '组织不存在',
  SOURCE_NOT_FOUND: '法源不存在',
  SERVICE_UNAVAILABLE: '服务暂不可用，请稍后重试',
  REQUEST_TIMEOUT: '请求超时，请重试',
  INTERNAL_SERVER_ERROR: '服务器内部错误，请稍后重试',
}

const DEFAULT_MESSAGES = {
  [ErrorKind.NETWORK]: '网络连接异常，请检查网络后重试',
  [ErrorKind.TIMEOUT]: '请求超时，请重试',
  [ErrorKind.OFFLINE]: '当前处于离线状态，请恢复网络后重试',
  [ErrorKind.CANCELLED]: '请求已取消',
  [ErrorKind.UNAUTHORIZED]: '登录状态已失效，请重新登录',
  [ErrorKind.FORBIDDEN]: '没有执行此操作的权限',
  [ErrorKind.CONFLICT]: '数据冲突，请刷新后重试',
  [ErrorKind.VALIDATION]: '请求参数校验失败，请检查输入后重试',
  [ErrorKind.RATE_LIMIT]: '操作过于频繁，请稍后再试',
  [ErrorKind.SERVER]: '服务器内部错误，请稍后重试',
  [ErrorKind.BUSINESS]: '操作失败，请稍后重试',
  [ErrorKind.UNKNOWN]: '发生未知错误，请稍后重试',
}

/** 从 axios 响应/错误中提取后端 envelope 字段（error.code / request_id / trace_id） */
function extractEnvelope(err) {
  const raw = err?.response && typeof err.response === 'object' ? err.response.data : null
  const error = raw && typeof raw === 'object' ? raw.error || null : null
  return {
    code: typeof error?.code === 'string' ? error.code : (typeof raw?.code === 'string' ? raw.code : ''),
    detail: error?.detail ?? raw?.detail ?? raw?.message ?? '',
    requestId: typeof raw?.request_id === 'string' ? raw.request_id : '',
    traceId: typeof raw?.trace_id === 'string' ? raw.trace_id : '',
  }
}

/**
 * 规范化任意错误为稳定结构。同一次调用结果会被缓存（__normalized）。
 * @param {unknown} err
 * @returns {{ kind: string, code: string, status?: number, message: string, detail: string, requestId: string, traceId: string }}
 */
export function normalizeError(err) {
  if (!err) {
    return { kind: ErrorKind.UNKNOWN, code: '', status: undefined, message: DEFAULT_MESSAGES[ErrorKind.UNKNOWN], detail: '', requestId: '', traceId: '' }
  }
  if (err && typeof err === 'object' && err.__normalized && typeof err.__normalized === 'object' && typeof err.__normalized.kind === 'string') {
    return err.__normalized
  }

  const { code, detail, requestId, traceId } = extractEnvelope(err)
  const status = typeof err?.response?.status === 'number' ? err.response.status : undefined

  let kind = ErrorKind.BUSINESS
  let message = CODE_TEXT[code] || ''

  const isCancel = err?.code === 'ERR_CANCELED' || err?.__canceled === true
  const isTimeout = err?.code === 'ECONNABORTED' || String(err?.message || '').toLowerCase().includes('timeout')

  if (isCancel) {
    kind = ErrorKind.CANCELLED
  } else if (isTimeout) {
    kind = ErrorKind.TIMEOUT
  } else if (!status) {
    // 无 HTTP 状态：网络层失败（axios 无响应）或离线
    kind = isOnline() ? ErrorKind.NETWORK : ErrorKind.OFFLINE
  } else if (status === 401) {
    kind = ErrorKind.UNAUTHORIZED
  } else if (status === 403) {
    kind = ErrorKind.FORBIDDEN
  } else if (status === 409) {
    kind = ErrorKind.CONFLICT
  } else if (status === 422) {
    kind = ErrorKind.VALIDATION
  } else if (status === 429) {
    kind = ErrorKind.RATE_LIMIT
  } else if (status >= 500) {
    kind = ErrorKind.SERVER
  }

  if (!message) {
    const fallbackDetail = typeof detail === 'string' && detail ? detail : ''
    const serverMessage = typeof err?.response?.data?.message === 'string' ? err.response.data.message : ''
    message = fallbackDetail || serverMessage || DEFAULT_MESSAGES[kind] || DEFAULT_MESSAGES[ErrorKind.BUSINESS]
  }

  const normalized = {
    kind,
    code,
    status,
    message,
    detail: typeof detail === 'string' ? detail : JSON.stringify(detail ?? ''),
    requestId,
    traceId,
    __normalized: true,
  }
  // 结果缓存到输入错误对象：同一错误多次规范化返回同一对象（幂等）
  if (err && typeof err === 'object') err.__normalized = normalized
  return normalized
}

/** 获取用户可读错误信息（页面兜底用，不展示堆栈/敏感内容） */
export function errorMessage(err) {
  return normalizeError(err).message
}

export function isConflict(err) {
  return normalizeError(err).kind === ErrorKind.CONFLICT
}

export function isCancelled(err) {
  return normalizeError(err).kind === ErrorKind.CANCELLED
}

export function isOfflineError(err) {
  return normalizeError(err).kind === ErrorKind.OFFLINE
}

export function isForbidden(err) {
  return normalizeError(err).kind === ErrorKind.FORBIDDEN
}

export function isUnauthorized(err) {
  return normalizeError(err).kind === ErrorKind.UNAUTHORIZED
}
