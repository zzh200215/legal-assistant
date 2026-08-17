import { ref, shallowRef, onScopeDispose } from 'vue'
import { normalizeError } from '../api/errors.js'
import { invalidateQueries, invalidateKey } from './cache.js'

// useMutation：统一写操作。职责：
//  - 自动生成/复用 Idempotency-Key：同一逻辑操作（连点/网络重试）复用同一 key，
//    成功后或业务失败后重置，避免重复创建资源/重复触发长任务
//  - 进行中连点合并（不依赖按钮禁用作为唯一保障）
//  - 不自动重试写请求（网络错误保留 key，用户重试时后端幂等兜底）
//  - 成功/失败回调 + 成功后精准失效相关查询（invalidate 精确 key，不做全局刷新）
//  - 支持 If-Match 版本头（配合 useQuery 捕获的 ETag）
//  - 错误统一 normalizeError 后抛出（页面消费稳定错误对象）

const KEEP_KEY_KINDS = new Set(['network', 'timeout', 'offline'])

function generateKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

/**
 * @param {object} options
 * @param {(variables: any, ctx: { idempotencyKey: string, ifMatch?: string }) => Promise<any>} options.mutationFn
 * @param {Array<Array|Function>} [options.invalidate] 成功后要失效的 query key（数组或 predicate）
 * @param {string} [options.idempotencyKey] 固定逻辑幂等键（可选，一般让 useMutation 自动管理）
 * @param {string} [options.ifMatch] 固定版本头（可选，也可在 mutate(vars, { ifMatch }) 按次传）
 */
export function useMutation(options) {
  const { mutationFn, onSuccess, onError, onSettled, invalidate = [] } = options
  const isPending = ref(false)
  const isSuccess = ref(false)
  const error = shallowRef(null)
  const data = shallowRef(undefined)
  const idempotencyKey = ref('')

  let pendingKey = null
  let inflight = null
  let active = true

  function resetKey() {
    pendingKey = null
    idempotencyKey.value = ''
  }

  /**
   * 执行写操作。进行中再次调用会合并到同一请求（同一逻辑幂等键）。
   * @param {any} variables
   * @param {{ idempotencyKey?: string, ifMatch?: string }} [callOptions]
   */
  function mutate(variables, callOptions = {}) {
    if (!active) return Promise.reject(new Error('mutation scope disposed'))
    if (inflight) return inflight
    if (!pendingKey) pendingKey = callOptions.idempotencyKey || options.idempotencyKey || generateKey()
    idempotencyKey.value = pendingKey
    isPending.value = true
    isSuccess.value = false
    error.value = null

    const promise = (async () => {
      try {
        const result = await mutationFn(variables, {
          idempotencyKey: pendingKey,
          ifMatch: callOptions.ifMatch !== undefined ? callOptions.ifMatch : options.ifMatch,
        })
        data.value = result
        isSuccess.value = true
        if (invalidate.length) {
          invalidate.forEach((keyOrPredicate) => {
            if (typeof keyOrPredicate === 'function') invalidateQueries(keyOrPredicate)
            else invalidateKey(keyOrPredicate)
          })
        }
        resetKey()
        onSuccess?.(result, variables)
        return result
      } catch (err) {
        const normalized = normalizeError(err)
        error.value = normalized
        // 网络/超时/离线：保留 key 便于重试时复用（后端幂等兜底）；其余失败重置为新的逻辑操作
        if (!KEEP_KEY_KINDS.has(normalized.kind)) resetKey()
        if (onError) {
          // 已由 onError 处理（展示文案等），不再向上抛，避免 fire-and-forget 调用产生未处理 rejection
          onError(normalized, variables)
          return undefined
        }
        throw normalized
      } finally {
        isPending.value = false
        inflight = null
        onSettled?.(data.value, error.value)
      }
    })()

    inflight = promise
    return promise
  }

  function reset() {
    error.value = null
    data.value = undefined
    isSuccess.value = false
    resetKey()
  }

  onScopeDispose(() => {
    active = false
    inflight = null
  })

  return {
    mutate,
    isPending,
    isSuccess,
    error,
    data,
    idempotencyKey,
    reset,
  }
}
