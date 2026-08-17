import { computed, isRef, ref, shallowRef, watch, onScopeDispose } from 'vue'
import { normalizeError, isCancelled } from '../api/errors.js'
import { isOnline, onNetworkChange } from '../utils/network.js'

// 统一长任务状态管理（WS 优先、指数退避轮询降级）：
//  - 状态：queued / running / retrying / partial / succeeded / failed / cancelled / expired
//  - WS 可用：订阅 jobs 通道事件（done/cancelled/run_snapshot/error）驱动状态；
//    WS 打开但长时间无该任务事件（后端未推送）→ 自动退避轮询兜底
//  - WS 断线/不可用：指数退避轮询 status_url
//  - 终态/取消/卸载/离线/页面隐藏即停止轮询与订阅；重新进入可按 job_id 恢复
//  - 取消调后端取消接口（幂等），不做假进度
//  - 不暴露堆栈/敏感内容（错误只保留 message/detail 摘要）

export const JobStatus = {
  QUEUED: 'queued',
  RUNNING: 'running',
  RETRYING: 'retrying',
  PARTIAL: 'partial',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
  EXPIRED: 'expired',
  IDLE: 'idle',
}

const TERMINAL = new Set([JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED])
const ACTIVE = new Set([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING, JobStatus.PARTIAL])

// Celery 状态 ↔ 统一状态
const CELERY_TO_JOB = {
  PENDING: JobStatus.QUEUED,
  RECEIVED: JobStatus.QUEUED,
  STARTED: JobStatus.RUNNING,
  PROCESSING: JobStatus.RUNNING,
  RETRY: JobStatus.RETRYING,
  SUCCESS: JobStatus.SUCCEEDED,
  FAILURE: JobStatus.FAILED,
  REVOKED: JobStatus.CANCELLED,
}

/** 统一状态 → Celery 风格状态（兼容既有 StatusTag async kind 展示） */
export function toCeleryState(status) {
  return {
    [JobStatus.QUEUED]: 'PENDING',
    [JobStatus.RUNNING]: 'PROCESSING',
    [JobStatus.RETRYING]: 'RETRY',
    [JobStatus.PARTIAL]: 'PROCESSING',
    [JobStatus.SUCCEEDED]: 'SUCCESS',
    [JobStatus.FAILED]: 'FAILURE',
    [JobStatus.CANCELLED]: 'REVOKED',
    [JobStatus.EXPIRED]: 'FAILURE',
    [JobStatus.IDLE]: 'PENDING',
  }[status] || 'PENDING'
}

const DEFAULT_POLL = { base: 1500, max: 30000, factor: 2 }
const WS_GRACE_MS = 5000 // WS 打开后等待首个任务事件的时间，超时转轮询兜底

/**
 * @param {object} options
 * @param {import('vue').Ref<number|null>|number|null} [options.jobId] 任务 id（响应式）
 * @param {(jobId: number, ctx: { signal?: AbortSignal }) => Promise<any>} [options.fetchStatus]
 *   状态拉取函数（返回 axios response 或业务值均可）
 * @param {(jobId: number) => Promise<any>} [options.cancelFn] 后端取消接口（幂等）
 * @param {object} [options.ws] { client, events } WS 客户端与任务事件映射（可选）
 * @param {boolean} [options.enabled=true]
 * @param {{ base?: number, max?: number, factor?: number }} [options.poll]
 * @param {(status: string, job: any) => void} [options.onTerminal]
 */
export function useAsyncJob(options) {
  const jobIdRef = isRef(options.jobId)
    ? options.jobId
    : typeof options.jobId === 'function'
      ? computed(options.jobId)
      : ref(options.jobId ?? null)
  const fetchStatus = options.fetchStatus
  const cancelFn = options.cancelFn
  const enabledRef = isRef(options.enabled)
    ? options.enabled
    : typeof options.enabled === 'function'
      ? computed(options.enabled)
      : ref(options.enabled ?? true)
  const poll = { ...DEFAULT_POLL, ...(options.poll || {}) }

  const status = ref(JobStatus.IDLE)
  const job = shallowRef(null) // 原始任务载荷（含 result 等）
  const message = ref('')
  const progress = ref(null)
  const error = shallowRef(null)
  const updatedAt = ref(null)
  const cancelRequested = ref(false)
  const source = ref('none') // none | ws | polling

  const isActive = computed(() => ACTIVE.has(status.value))
  const isTerminal = computed(() => TERMINAL.has(status.value))

  let generation = 0
  let pollTimer = null
  let wsGraceTimer = null
  let wsEventTimer = null
  let unsubscribeNetwork = null
  let wsClient = options.ws?.client || null
  let wsListener = null
  let offlinePaused = false

  function resetJob() {
    generation += 1
    stopPolling()
    clearWsTimers()
    status.value = JobStatus.IDLE
    job.value = null
    message.value = ''
    progress.value = null
    error.value = null
    cancelRequested.value = false
    source.value = 'none'
  }

  function stopPolling() {
    if (pollTimer) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  function clearWsTimers() {
    if (wsGraceTimer) {
      clearTimeout(wsGraceTimer)
      wsGraceTimer = null
    }
    if (wsEventTimer) {
      clearTimeout(wsEventTimer)
      wsEventTimer = null
    }
  }

  function applyPayload(payload) {
    if (!payload || typeof payload !== 'object') return
    const raw = payload
    let nextStatus = null
    let nextMessage
    let nextProgress = null

    if (typeof raw.status === 'string') {
      const s = raw.status.toUpperCase()
      nextStatus = CELERY_TO_JOB[s] || raw.status.toLowerCase()
    } else if (typeof raw.state === 'string') {
      nextStatus = CELERY_TO_JOB[raw.state.toUpperCase()] || raw.state.toLowerCase()
    } else if (raw.type === 'done') {
      nextStatus = JobStatus.SUCCEEDED
    } else if (raw.type === 'cancelled') {
      nextStatus = raw.cancelled === false ? JobStatus.RUNNING : JobStatus.CANCELLED
    } else if (raw.type === 'run_snapshot') {
      nextStatus = JobStatus.RUNNING
    } else if (raw.type === 'error') {
      nextStatus = JobStatus.FAILED
    }
    if (!nextStatus) return

    if (raw.type === 'done') nextMessage = '任务已完成'
    else if (raw.type === 'cancelled') nextMessage = raw.cancelled === false ? '' : '任务已取消'
    else nextMessage = raw.message || raw.info?.step || raw.error?.message || (typeof raw.error === 'string' ? raw.error : '') || ''

    if (typeof raw.progress === 'number') nextProgress = raw.progress

    status.value = nextStatus
    job.value = raw
    message.value = nextMessage
    progress.value = nextProgress
    updatedAt.value = Date.now()
    error.value = nextStatus === JobStatus.FAILED ? normalizeError({ response: { status: 500, data: { detail: nextMessage } } }) : null
  }

  async function fetchOnce() {
    const gen = generation
    try {
      const result = await fetchStatus(jobIdRef.value, {})
      if (gen !== generation) return true
      const body = result && typeof result === 'object' && 'data' in result && 'status' in result ? result.data : result
      applyPayload(body)
      if (TERMINAL.has(status.value)) {
        stop()
        options.onTerminal?.(status.value, job.value)
        return false
      }
      return true
    } catch (err) {
      if (gen !== generation) return true
      if (isCancelled(err)) return true
      error.value = normalizeError(err)
      return true // 瞬时失败：按退避重试
    }
  }

  function schedulePoll(attempt) {
    if (!enabledRef.value || offlinePaused || TERMINAL.has(status.value)) return
    const delay = Math.min(poll.base * poll.factor ** attempt, poll.max)
    pollTimer = setTimeout(async () => {
      const cont = await fetchOnce()
      if (cont && !TERMINAL.has(status.value)) schedulePoll(attempt + 1)
    }, delay)
  }

  function startPolling() {
    stopPolling()
    source.value = 'polling'
    schedulePoll(0)
  }

  function onWsEvent(event) {
    if (!event || typeof event !== 'object') return
    const isRelevant = ['done', 'cancelled', 'run_snapshot', 'error', 'welcome', 'resync_required'].includes(event.type)
    if (!isRelevant) return
    if (wsGraceTimer) {
      clearTimeout(wsGraceTimer)
      wsGraceTimer = null
    }
    if (event.type === 'done' || event.type === 'cancelled' || event.type === 'run_snapshot' || event.type === 'error') {
      // 任务事件到达 → WS 通道可用，停止轮询
      stopPolling()
      source.value = 'ws'
      applyPayload(event)
      if (TERMINAL.has(status.value)) {
        stop()
        options.onTerminal?.(status.value, job.value)
      }
    }
  }

  function connectWs() {
    if (!wsClient || offlinePaused) return
    wsClient.connect()
    wsGraceTimer = setTimeout(() => {
      // WS 已打开但未收到任务事件（后端未推送该任务）→ 轮询兜底
      if (!TERMINAL.has(status.value) && enabledRef.value) startPolling()
    }, WS_GRACE_MS)
  }

  function stopWs() {
    clearWsTimers()
  }

  function stop() {
    stopPolling()
    stopWs()
    generation += 1
  }

  function start() {
    const id = jobIdRef.value
    if (!id || !enabledRef.value) return
    resetJob()
    status.value = JobStatus.QUEUED
    if (wsClient && isOnline()) {
      connectWs()
    } else {
      startPolling()
    }
  }

  /** 取消任务（调后端取消接口，幂等；已请求取消或终态为 no-op） */
  async function cancel() {
    const id = jobIdRef.value
    if (!id || TERMINAL.has(status.value) || cancelRequested.value) return
    cancelRequested.value = true
    if (cancelFn) {
      try {
        await cancelFn(id)
      } catch (err) {
        if (!isCancelled(err)) {
          error.value = normalizeError(err)
        }
      }
    }
  }

  /** 重新开始观察同一 job（页面重进入/手动刷新） */
  function refresh() {
    if (TERMINAL.has(status.value)) {
      resetJob()
      start()
    } else if (jobIdRef.value) {
      generation += 1
      stopPolling()
      startPolling()
    }
  }

  watch(jobIdRef, () => {
    if (TERMINAL.has(status.value)) resetJob()
    start()
  })

  // WS 事件订阅：任务状态事件优先驱动（WS 打开但长时间无事件时轮询兜底）
  if (wsClient && typeof wsClient.addListener === 'function') {
    wsListener = (event) => onWsEvent(event)
    wsClient.addListener(wsListener)
    wsClient.subscribe(options.ws?.channels || ['jobs'])
  }

  watch(enabledRef, (value) => {
    if (value) start()
    else {
      generation += 1
      stop()
    }
  })

  unsubscribeNetwork = onNetworkChange((online) => {
    if (online) {
      offlinePaused = false
      if (enabledRef.value && jobIdRef.value && !TERMINAL.has(status.value)) {
        if (wsClient) connectWs()
        else startPolling()
      }
    } else {
      offlinePaused = true
      stopPolling()
    }
  })

  // 页面隐藏时停止轮询；可见且任务活跃时恢复
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function onVisibilityChange() {
    if (document.hidden) {
      stopPolling()
    } else if (enabledRef.value && jobIdRef.value && !TERMINAL.has(status.value) && isOnline()) {
      if (wsClient) connectWs()
      else startPolling()
    }
  }

  onScopeDispose(() => {
    generation += 1
    stop()
    if (wsClient && wsListener && typeof wsClient.removeListener === 'function') {
      wsClient.removeListener(wsListener)
    }
    if (unsubscribeNetwork) unsubscribeNetwork()
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  if (jobIdRef.value && enabledRef.value) start()

  return {
    jobId: jobIdRef,
    status,
    job,
    message,
    progress,
    error,
    updatedAt,
    cancelRequested,
    source,
    isActive,
    isTerminal,
    cancel,
    refresh,
    reset: resetJob,
    stop,
    start,
  }
}
