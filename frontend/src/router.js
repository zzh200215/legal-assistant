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
const LegalDeveloper = () => import('./views/LegalDeveloper.vue')
const LegalOnboarding = () => import('./views/LegalOnboarding.vue')
const Pricing = () => import('./views/Pricing.vue')

// meta.capability：路由级权限门禁（App.vue 统一渲染 403 状态，直接访问受保护路由不静默放行）。
// 仅对后端确有管理门槛的页面启用（/system 的 /admin/*、/org/* 接口为管理员权限）；
// /tasks、/agent 后端对各登录用户开放（get_current_user），导航入口按产品规则仅管理员显示，
// 直接路由访问保持既有可用行为。前端权限仅用于 UX 控制；后端接口仍做服务端校验。
const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/', redirect: '/legal-onboarding' },
  { path: '/legal-workspace', component: LegalWorkspace, meta: { capability: CAPABILITY.WORKSPACE_MANAGE } },
  { path: '/documents', component: Documents, meta: { capability: CAPABILITY.DOCUMENT_READ } },
  { path: '/tasks', component: Tasks },
  { path: '/agent', component: Agent },
  { path: '/chat', component: Chat },
  { path: '/system', component: System, meta: { capability: CAPABILITY.SYSTEM_VIEW } },
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
