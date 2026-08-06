import http from './http'

// 后端 subscription_api 挂在 /api/billing 前缀下（app/main.py），路径必须带 billing。
export default {
  listPlans() {
    return http.get('/billing/plans')
  },
  mySubscription() {
    return http.get('/billing/subscriptions/me')
  },
  myQuota() {
    return http.get('/billing/subscriptions/quota')
  },
  checkout(tier) {
    return http.post('/billing/subscriptions/checkout', null, { params: { tier } })
  },
  cancelSubscription() {
    return http.post('/billing/subscriptions/cancel')
  },
}
