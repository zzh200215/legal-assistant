import { createRouter, createWebHistory } from 'vue-router'

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

const routes = [
  { path: '/login', component: Login, meta: { public: true } },
  { path: '/', redirect: '/legal-onboarding' },
  { path: '/legal-workspace', component: LegalWorkspace },
  { path: '/documents', component: Documents },
  { path: '/tasks', component: Tasks },
  { path: '/agent', component: Agent },
  { path: '/chat', component: Chat },
  { path: '/system', component: System },
  { path: '/legal-developer', component: LegalDeveloper },
  { path: '/legal-onboarding', component: LegalOnboarding },
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
