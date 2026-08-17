import { computed, isRef, ref, shallowRef, watch, onScopeDispose } from 'vue'
import { getEntry, subscribe, emitSerialized, serializeKey, invalidateQueries, invalidateKey, getQueryData, setQueryData, removeQuery, clearQueryCache } from './cache.js'
import { normalizeError, isCancelled } from '../api/errors.js'
import { isOnline, onNetworkChange } from '../utils/network.js'

// useQuery：统一只读查询。职责：
//  - query key 规范化 + 缓存（staleTime）与同 key 去重（共享 in-flight promise）
//  - AbortController 取消：路由切换/组件卸载时取消无用请求（引用计数，最后一个订阅者离开才 abort）
//  - 仅幂等 GET 自动重试（指数退避；网络/超时/5xx；4xx 不重试；离线暂停）
//  - loading/fetching/error/stale/offline 状态 + request_id/trace_id/ETag 元数据
//  - 离线自动暂停、恢复在线自动补拉
// 组件外测试可用 @vue/reactivity 的 effectScope 包裹（onScopeDispose 生效）。

const RETRYABLE_KINDS = new Set(['network', 'timeout', 'server'])

function isAxiosResponseLike(value) {
  return Boolean(value && typeof value === 'object' && 'status' in value && 'headers' in value)
}

function unwrap(result) {
  if (isAxiosResponseLike(result)) return result.data
  return result
}

function extractMeta(result) {
  if (!isAxiosResponseLike(result)) return { requestId: '', traceId: '', etag: '' }
  const body = result.data && typeof result.data === 'object' ? result.data : {}
  return {
    requestId: typeof body.request_id === 'string' ? body.request_id : '',
    traceId: typeof body.trace_id === 'string' ? body.trace_id : '',
    etag: typeof result.headers?.etag === 'string' ? result.headers.etag : '',
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * @param {object} options
 * @param {Array|Function} options.key 查询 key（数组或返回数组的函数，可含响应式状态）
 * @param {(ctx: { signal: AbortSignal }) => Promise<any>} options.fetcher
 * @param {number} [options.staleTime=0] 缓存新鲜时长（ms）；0 表示每次挂载都重取
 * @param {number} [options.retry=1] 幂等读失败重试次数
 * @param {(attempt: number) => number} [options.retryDelay] 退避延迟（ms）
 * @param {boolean|Function} [options.enabled=true] 是否启用
 */
export function useQuery(options) {
  const keyRef = computed(() => (typeof options.key === 'function' ? options.key() : options.key))
  const fetcherRef = options.fetcher
  const staleTimeRef = ref(options.staleTime ?? 0)
  const retryRef = ref(options.retry ?? 1)
  const retryDelayRef = options.retryDelay || ((attempt) => 500 * 2 ** attempt)
  const enabledRef = isRef(options.enabled)
    ? options.enabled
    : typeof options.enabled === 'function'
      ? computed(options.enabled)
      : ref(Boolean(options.enabled ?? true))

  const data = shallowRef(undefined)
  const error = shallowRef(null)
  const status = ref('idle') // idle | loading | success | error
  const isFetching = ref(false)
  const isStale = ref(false)
  const isRefetching = ref(false)
  const requestId = ref('')
  const traceId = ref('')
  const etag = ref(null)
  const fetchedAt = ref(0)

  let entry = null
  let currentSerialized = null
  let unsubscribeEntry = null
  let unsubscribeNetwork = null
  let retryTimer = null
  let active = true
  let generation = 0
  let lastVersion = 0 // 已消费的 entry 失效版本：仅 invalidate 触发重取，避免普通刷新通知造成重取风暴

  function syncFromEntry() {
    if (!entry) return
    data.value = entry.data
    error.value = entry.error
    status.value = entry.status
    const hasData = entry.data !== undefined
    isStale.value = hasData && (entry.staleAt === 0 || Date.now() >= entry.staleAt)
    isFetching.value = Boolean(entry.fetching)
    isRefetching.value = Boolean(entry.fetching) && hasData
    requestId.value = entry.meta?.requestId || ''
    traceId.value = entry.meta?.traceId || ''
    etag.value = entry.meta?.etag || null
    fetchedAt.value = entry.fetchedAt || 0
  }

  function leaveEntry() {
    if (unsubscribeEntry) {
      unsubscribeEntry()
      unsubscribeEntry = null
    }
    if (entry) {
      entry.subscribers = Math.max(0, entry.subscribers - 1)
      if (entry.subscribers === 0 && entry.fetching && entry.controller) {
        // 最后一个订阅者离开：取消进行中的请求，避免旧响应残留
        entry.controller.abort()
      }
      entry = null
      currentSerialized = null
    }
  }

  function enterEntry(keyValue) {
    currentSerialized = serializeKey(keyValue)
    entry = getEntry(keyValue)
    entry.subscribers += 1
    lastVersion = entry.version
    unsubscribeEntry = subscribe(keyValue, () => {
      if (!active || !entry) return
      const versionAtNotify = entry.version
      syncFromEntry()
      // 仅失效（version 变更）触发重取；普通数据刷新通知只同步状态
      if (versionAtNotify > lastVersion) {
        lastVersion = versionAtNotify
        decideFetch()
      }
    })
  }

  async function runFetch() {
    if (!entry || !active) return
    if (!enabledRef.value) return
    if (!isOnline()) return // 离线不发起请求；恢复在线时自动补拉

    if (entry.fetching) {
      await entry.fetching
      syncFromEntry()
      return
    }

    const myGen = ++generation
    const controller = new AbortController()
    entry.controller = controller
    entry.status = 'loading'
    syncFromEntry()

    const promise = (async () => {
      let attempt = 0
      for (;;) {
        try {
          const result = await fetcherRef({ signal: controller.signal })
          if (!active || myGen !== generation) return
          entry.data = unwrap(result)
          entry.error = null
          entry.status = 'success'
          entry.fetchedAt = Date.now()
          entry.staleAt = Date.now() + staleTimeRef.value
          entry.meta = extractMeta(result)
          return
        } catch (err) {
          if (!active || myGen !== generation) return
          if (isCancelled(err) || controller.signal.aborted) return // 取消：保留旧数据
          const normalized = normalizeError(err)
          const retryable = RETRYABLE_KINDS.has(normalized.kind)
          if (retryable && attempt < retryRef.value && isOnline()) {
            attempt += 1
            await sleep(retryDelayRef(attempt))
            if (controller.signal.aborted) return
            continue
          }
          entry.error = normalized
          entry.status = 'error'
          return
        }
      }
    })()

    entry.fetching = promise
    try {
      await promise
    } finally {
      if (myGen === generation) {
        entry.fetching = null
        entry.controller = null
        if (active && entry.subscribers > 0) emitSerialized(currentSerialized)
      }
      syncFromEntry()
    }
  }

  function decideFetch() {
    if (!entry || !active) return
    if (!enabledRef.value) return
    if (!isOnline()) return
    // 错误态不自动重试（避免 emit 通知造成重试风暴）；重试由显式 refetch/invalidate/重新挂载/网络恢复触发
    if (entry.status === 'error') return
    const hasData = entry.data !== undefined
    if (hasData && entry.staleAt !== 0 && Date.now() < entry.staleAt) return // 新鲜缓存
    runFetch()
  }

  function refetch() {
    if (!entry) return undefined
    runFetch()
    return entry?.fetching || undefined
  }

  function invalidate() {
    if (!entry) return
    entry.staleAt = 0
    entry.version += 1
    emitSerialized(currentSerialized)
    runFetch()
  }

  // 首次进入：错误态缓存显式重取（重新挂载），否则按新鲜度决定
  enterEntry(keyRef.value)
  syncFromEntry()
  if (entry.status === 'error') runFetch()
  else decideFetch()

  // key 变化：离开旧 entry，进入新 entry 并决定是否重取
  watch(keyRef, (next, prev) => {
    if (serializeKey(next) === serializeKey(prev)) return
    leaveEntry()
    enterEntry(next)
    syncFromEntry()
    decideFetch()
  })

  // 网络恢复：错误态显式重取，stale 数据按新鲜度补拉
  // enabled 变化：变为 true 时按新鲜度决定取数；变为 false 时废弃在途请求
  watch(enabledRef, (value) => {
    if (!active) return
    if (value) decideFetch()
    else generation += 1
  })

  unsubscribeNetwork = onNetworkChange((online) => {
    if (!active) return
    if (online && entry) {
      if (retryTimer) {
        clearTimeout(retryTimer)
        retryTimer = null
      }
      if (entry.status === 'error') runFetch()
      else if (isStale.value) decideFetch()
    }
  })

  onScopeDispose(() => {
    active = false
    generation += 1
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (unsubscribeNetwork) {
      unsubscribeNetwork()
      unsubscribeNetwork = null
    }
    leaveEntry()
  })

  return {
    data,
    error,
    status,
    isFetching,
    isRefetching,
    isStale,
    isLoading: computed(() => status.value === 'loading' && data.value === undefined),
    isError: computed(() => status.value === 'error'),
    isSuccess: computed(() => status.value === 'success'),
    requestId,
    traceId,
    etag,
    fetchedAt,
    refetch,
    invalidate,
  }
}

/** 全局查询客户端：mutation 成功后的精准失效入口 */
export function useQueryClient() {
  return {
    invalidateQueries,
    invalidateKey,
    getQueryData,
    setQueryData,
    removeQuery,
    clearQueryCache,
  }
}
