// 内存查询缓存（零依赖）：useQuery/useMutation 的共享协调层。
// - 同 key 去重：entry.fetching 持有进行中的 Promise，多个消费者共享同一请求。
// - 失效：invalidateQueries(predicate) 使匹配 key 变 stale 并通知活跃订阅者重取。
// - 取消：entry 级别 AbortController；最后一个订阅者离开时才 abort（引用计数）。
// 缓存数据不跨页面复制进 store，避免服务端数据多副本漂移。

const entries = new Map() // serializedKey -> entry
const listeners = new Map() // serializedKey -> Set<() => void>

export function serializeKey(key) {
  return JSON.stringify(key, (k, v) => (typeof v === 'function' ? undefined : v))
}

function createEntry() {
  return {
    data: undefined, // 已解包的数据（axios response.data 之后的业务值）
    error: null, // 规范化错误对象
    status: 'idle', // idle | loading | success | error
    fetching: null, // 进行中的 fetch Promise（去重用）
    controller: null, // entry 级 AbortController
    version: 0, // 失效版本：invalidate 时 +1
    staleAt: 0, // 超过该时间视为 stale（0 = 立即 stale）
    fetchedAt: 0,
    meta: { requestId: '', traceId: '', etag: '' }, // envelope 元数据（错误展示 / 版本冲突）
    subscribers: 0,
  }
}

export function getEntry(key) {
  const s = serializeKey(key)
  let entry = entries.get(s)
  if (!entry) {
    entry = createEntry()
    entries.set(s, entry)
  }
  return entry
}

export function hasEntry(key) {
  return entries.has(serializeKey(key))
}

export function subscribe(key, fn) {
  const s = serializeKey(key)
  let set = listeners.get(s)
  if (!set) {
    set = new Set()
    listeners.set(s, set)
  }
  set.add(fn)
  return () => {
    set.delete(fn)
    if (set.size === 0) listeners.delete(s)
  }
}

export function emit(key) {
  emitSerialized(serializeKey(key))
}

/** 内部：用已序列化的 key 通知（避免二次序列化） */
export function emitSerialized(serializedKey) {
  const set = listeners.get(serializedKey)
  if (!set) return
  set.forEach((fn) => {
    try {
      fn()
    } catch {
      // 单个订阅者异常不影响其他订阅者
    }
  })
}

/** 读取缓存数据（供 mutation 成功后的本地合并/预写） */
export function getQueryData(key) {
  const entry = entries.get(serializeKey(key))
  return entry ? entry.data : undefined
}

/** 直接写入缓存（预写/乐观更新）；默认同时视为 fresh */
export function setQueryData(key, data) {
  const entry = getEntry(key)
  entry.data = data
  entry.status = 'success'
  entry.error = null
  entry.fetchedAt = Date.now()
  entry.staleAt = 0
  entry.meta = { ...entry.meta, requestId: '', traceId: '', etag: '' }
  emit(key)
}

export function removeQuery(key) {
  const s = serializeKey(key)
  entries.delete(s)
  emitSerialized(s)
}

/**
 * 使匹配 key 失效：version+1、立即 stale，并通知活跃订阅者重取。
 * @param {(serializedKey: string) => boolean} predicate
 * @returns {number} 命中的 key 数量
 */
export function invalidateQueries(predicate) {
  let count = 0
  entries.forEach((entry, s) => {
    if (predicate(s)) {
      entry.version += 1
      entry.staleAt = 0
      emitSerialized(s)
      count += 1
    }
  })
  return count
}

/** 使单个 key 失效 */
export function invalidateKey(key) {
  return invalidateQueries((s) => s === serializeKey(key))
}

/** 清理全部缓存（登出时调用） */
export function clearQueryCache() {
  entries.clear()
  listeners.clear()
}
