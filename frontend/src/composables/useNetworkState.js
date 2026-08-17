import { useOnline } from '../utils/network'

// 组件内网络状态钩子：offline 状态下页面展示缓存 + 离线横幅，禁止不安全写操作。
export function useNetworkState() {
  const online = useOnline()
  return {
    online,
    isOnline: online,
  }
}
