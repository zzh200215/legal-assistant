// 401 单飞刷新协调器（纯逻辑，可单测）：
//  - 并发 401 共享同一个 refresh 流程（只触发一次刷新）
//  - 刷新失败只回调一次 onExpired（按现有认证流程登出）
export function createRefreshCoordinator({ refreshFn, onExpired }) {
  let inFlight = null

  async function refresh() {
    if (!inFlight) {
      inFlight = refreshFn()
        .catch((err) => {
          // 刷新失败：只回调一次 onExpired（并发等待方共享同一 promise）
          onExpired()
          throw err
        })
        .finally(() => {
          inFlight = null
        })
    }
    return inFlight
  }

  /** 尝试恢复会话：成功返回 true（调用方重放原请求）；失败返回 false（onExpired 已触发一次） */
  async function recover() {
    try {
      await refresh()
      return true
    } catch {
      return false
    }
  }

  return {
    refresh,
    recover,
    get inFlight() {
      return inFlight
    },
  }
}
