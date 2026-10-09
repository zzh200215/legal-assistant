import { createRouter, createWebHistory } from 'vue-router'
import { CAPABILITY } from './auth/capabilities'

const LegalWorkspace = () => import('./views/LegalWorkspace.vue')
const LegalPortal = () => import('./views/LegalPortal.vue')
const Chat = () => import('./views/Chat.vue')
const Documents = () => import('./views/Documents.vue')
const Tasks = () => import('./views/Tasks.vue')
const Agent = () => import('./views/Agent.vue')
const System = () => import('./views/System.vue')
const Login = () => import('./views/Login.vue')
const ResetPassword = () => import('./views/ResetPassword.vue')
const LegalDeveloper = () => import('./views/LegalDeveloper.vue')
const LegalOnboarding = () => import('./views/LegalOnboarding.vue')
const Pricing = () => import('./views/Pricing.vue')
const ManagementCenter = () => import('./views/ManagementCenter.vue')
const Notifications = () => import('./views/Notifications.vue')

// meta.capability：路由级权限门禁（App.vue 统一渲染 403 状态，直接访问受保护路由不静默放行）。
// 仅对后端确有管理门槛的页面启用（/system 的 /admin/*、/org/* 接口为管理员权限）；
// /tasks、/agent 后端对各登录用户开放（get_current_user），导航入口按产品规则仅管理员显示，
// 直接路由访问保持既有可用行为。前端权限仅用于 UX 控制；后端接口仍做服务端校验。
const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  // 密码重置落地页（承接重置邮件链接 {前端地址}/reset-password?token=xxx）
  { path: '/reset-password', component: ResetPassword, meta: { public: true } },
  { path: '/', redirect: '/legal-workspace' },
  { path: '/workbench', redirect: '/legal-workspace' },
  { path: '/cases', redirect: '/legal-workspace?view=cases' },
  { path: '/legal-research', redirect: '/legal-workspace?view=research' },
  { path: '/legal-review', redirect: '/legal-workspace?view=review' },
  { path: '/legal-workspace', component: LegalWorkspace, meta: { capability: CAPABILITY.WORKSPACE_MANAGE } },
  { path: '/documents', component: Documents, meta: { capability: CAPABILITY.DOCUMENT_READ } },
  { path: '/tasks', component: Tasks },
  { path: '/notifications', component: Notifications },
  { path: '/agent', component: Agent },
  { path: '/chat', component: Chat },
  { path: '/system', component: System, meta: { capability: CAPABILITY.SYSTEM_VIEW } },
  { path: '/management', component: ManagementCenter, meta: { capability: CAPABILITY.SYSTEM_VIEW } },
  { path: '/legal-developer', component: LegalDeveloper },
  { path: '/legal-onboarding', component: LegalOnboarding, meta: { capability: CAPABILITY.WORKSPACE_MANAGE } },
  { path: '/pricing', component: Pricing },
  { path: '/portal/c/:token', component: LegalPortal, meta: { public: true } },
  { path: '/tokens', redirect: '/system?tab=tokens' },
  { path: '/oplogs', redirect: '/system?tab=oplogs' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path === '/login' && token) {
    return '/'
  }

  if (!to.meta?.public && !token) {
    return '/login'
  }
})

export default router
