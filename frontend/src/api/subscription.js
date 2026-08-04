import http from './http'

export default {
  listPlans() {
    return http.get('/subscriptions/plans')
  },
  mySubscription() {
    return http.get('/subscriptions/me')
  },
  myQuota() {
    return http.get('/subscriptions/quota')
  },
  checkout(tier) {
    return http.post('/subscriptions/checkout', null, { params: { tier } })
  },
  cancelSubscription() {
    return http.post('/subscriptions/cancel')
  },
}
