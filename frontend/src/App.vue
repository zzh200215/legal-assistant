<template>
  <router-view v-if="isPublicRoute" v-slot="{ Component }">
    <transition name="page-fade" mode="out-in">
      <component :is="Component" />
    </transition>
  </router-view>

  <div v-else class="app-shell">
    <aside class="topbar">
      <div class="topbar-brand">
        <div class="topbar-logo">律</div>
        <div class="topbar-title">
          <span class="topbar-name">律智检</span>
          <span class="topbar-tag">企业法务与律师工作台</span>
        </div>
      </div>

      <nav class="top-nav" aria-label="主导航">
        <button
          v-for="item in visibleNavItems(navItems)"
          :key="item.path"
          class="nav-entry"
          :class="isRouteActive(item) ? 'active' : ''"
          :title="item.caption"
          @click="onMenuSelect(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>

        <template v-for="group in visibleNavGroups" :key="group.label">
          <div class="nav-group-label">{{ group.label }}</div>
          <button
            v-for="item in group.items"
            :key="item.path"
            class="nav-entry"
            :class="isRouteActive(item) ? 'active' : ''"
            :title="`${group.label} · ${item.caption}`"
            @click="onMenuSelect(item.path)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar-footer">
        <div class="utility-row">
          <button class="utility-link" @click="onMenuSelect('/legal-onboarding')">
            <span class="guide-dot" aria-hidden="true"></span>
            <span class="utility-label">使用指南</span>
          </button>
          <button v-if="authStore.ready && authStore.isAdmin" class="utility-link" @click="onMenuSelect('/system')">
            <span class="status-dot" aria-hidden="true"></span>
            <span class="utility-label">平台状态</span>
          </button>
          <NotificationBell />
        </div>
        <div class="account-row">
          <span class="account-avatar" aria-hidden="true">{{ accountInitial }}</span>
          <span class="account-meta">
            <span class="account-name">{{ user?.username || '未登录' }}</span>
            <span class="account-role">{{ roleLabel }}</span>
          </span>
          <button class="logout-btn" @click="logout">退出</button>
        </div>
      </div>
    </aside>

    <div class="app-workspace">
    <OfflineBanner />
    <section v-if="!isLegalWorkspaceRoute" class="section-strip">
      <div class="section-heading">
        <h1>{{ currentSection.label }}</h1>
        <p>{{ currentSection.description }}</p>
      </div>
    </section>

    <main class="main-content">
      <div v-if="routeAccess === 'loading'" class="route-loading" role="status">
        <div class="route-loading-card">正在加载…</div>
      </div>
      <ForbiddenState v-else-if="routeAccess === 'deny'" :required="route.meta?.capability" />
      <router-view v-else v-slot="{ Component }">
        <transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
    </div>

    <nav class="mobile-nav" aria-label="移动端导航">
      <button
        v-for="item in mobileNavItems"
        :key="item.path"
        class="mobile-nav-item"
        :class="isRouteActive(item) ? 'active' : ''"
        @click="onMenuSelect(item.path)"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span>{{ item.label }}</span>
      </button>
    </nav>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Check, Cpu, DataLine, Files, House, List, Notebook, Reading, ScaleToOriginal, Tickets } from '@element-plus/icons-vue'
import api from './api'
import { setAccessToken, setRefreshToken } from './api/http'
import { clearQueryCache } from './query/cache'
import OfflineBanner from './components/OfflineBanner.vue'
import ForbiddenState from './components/ForbiddenState.vue'
import NotificationBell from './components/legal/NotificationBell.vue'
import { useAuthStore } from './stores/auth'

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()
const user = ref(null)
const isPublicRoute = computed(() => route.meta?.public === true)

// 路由级 capability 门禁：权限未知（auth 未就绪）默认不放行，直接访问受保护路由显示 403 状态
const routeAccess = computed(() => {
  const required = route.meta?.capability
  if (!required) return 'allow'
  if (!authStore.ready) return 'loading'
  return authStore.capabilities.includes(required) ? 'allow' : 'deny'
})

const navItems = [
  { path: '/legal-workspace', label: '工作台', caption: '案件进展与待处理事项', icon: House, match: (r) => r.path === '/legal-workspace' && !r.query.view },
  { path: '/cases', label: '案件', caption: '案件与案件工作记录', icon: Tickets, match: (r) => r.path === '/legal-workspace' && r.query.view === 'cases' },
  { path: '/documents', label: '文档', caption: '案件材料与法律文件', icon: Files },
  { path: '/legal-workspace?view=research', label: '法律研究', caption: '法规、案例与业务资料', icon: Reading, match: (r) => r.path === '/legal-workspace' && r.query.view === 'research' },
  { path: '/legal-workspace?view=review', label: '审核', caption: '待处理律师审核', icon: Check, match: (r) => r.path === '/legal-workspace' && r.query.view === 'review' },
  { path: '/tasks', label: '任务', caption: '案件协作事项与处理进度', icon: List },
]

const navGroups = [
  {
    label: '管理中心',
    items: [
      { path: '/management', label: '管理中心', caption: '平台设置与运营', icon: DataLine, adminOnly: true },
      { path: '/system', label: '系统', caption: '组织、权限与平台运行', icon: DataLine, adminOnly: true, match: (r) => r.path === '/system' && r.query.tab !== 'experiments' },
      { path: '/agent', label: 'Agent', caption: '自动化执行与运行记录', icon: Cpu, adminOnly: true },
      { path: '/system?tab=experiments', label: '评测与审计', caption: '质量评测与操作记录', icon: Notebook, adminOnly: true, match: (r) => r.path === '/system' && r.query.tab === 'experiments' },
      { path: '/pricing', label: '计费', caption: '订阅方案与使用情况', icon: ScaleToOriginal, adminOnly: true },
    ],
  },
]

const sectionMeta = {
  '/legal-workspace': { label: '工作台', description: '案件进展与待处理事项' },
  '/cases': { label: '案件', description: '管理案件与案件工作记录' },
  '/': { label: '工作台', description: '案件进展与待处理事项' },
  // `/` 会重定向到 `/legal-onboarding`，缺这条会退回默认值、把产品名当页面标题重复一次
  '/legal-onboarding': { label: '开始使用', description: '按角色完成初始配置，然后进入法律工作台' },
  '/legal-developer': { label: '开发者设置', description: 'API 凭据、Webhook 与集成调试' },
  '/pricing': { label: '计费', description: '订阅方案、用量与账单' },
  '/documents': { label: '文档', description: '案件材料、合同与法律文件' },
  '/tasks': { label: '任务', description: '案件协作事项与处理进度' },
  '/notifications': { label: '通知中心', description: '案件动作、审核和关键日期提醒' },
  '/agent': { label: 'Agent', description: '自动化执行与运行记录' },
  '/chat': { label: '案件助手', description: '基于案件资料开展法律工作' },
  '/system': { label: '系统', description: '组织、权限、策略与平台运行' },
  '/management': { label: '管理中心', description: '平台设置、质量与运营' },
}

const isLegalWorkspaceRoute = computed(() => route.path === '/legal-workspace')
const currentSection = computed(() => {
  if (route.path === '/legal-workspace') {
    const view = route.query.view
    if (view === 'cases') return { label: '案件', description: '管理案件与案件工作记录' }
    if (view === 'research') return { label: '法律研究', description: '法规、案例与业务资料' }
    if (view === 'review') return { label: '审核', description: '律师复核与工作流转' }
    return sectionMeta[route.path]
  }
  return sectionMeta[route.path] || { label: '律智检', description: '法律工作台' }
})
const visibleNavGroups = computed(() =>
  navGroups
    .map((group) => ({ ...group, items: visibleNavItems(group.items) }))
    .filter((group) => group.items.length)
)

// 导航可见性沿用产品规则（adminOnly）；路由级 capability 门禁独立于导航（router meta.capability）
const canShow = (item) => {
  if (!item.adminOnly) return true
  return authStore.ready && authStore.isAdmin
}
const visibleNavItems = (items) => items.filter(canShow)
const mobileNavItems = computed(() => visibleNavItems(navItems).slice(0, 6))
const isRouteActive = (item) => item.match ? item.match(route) : route.path === item.path
const onMenuSelect = (path) => router.push(path)

const accountInitial = computed(() => (user.value?.username || '?').slice(0, 1).toUpperCase())
const roleLabel = computed(() => {
  if (!user.value?.role) return '未登录'
  return user.value.role === 'admin' ? '管理员' : '成员'
})

const logout = () => {
  setAccessToken(null)
  setRefreshToken(null)
  localStorage.removeItem('user_role')
  clearQueryCache()
  authStore.clear()
  router.push('/login')
}

onMounted(async () => {
  if (isPublicRoute.value) return
  try {
    const { data } = await api.getMe()
    user.value = data
    authStore.setUser(data)
    localStorage.setItem('user_role', data.role || 'user')
  } catch {
    user.value = null
    authStore.clear()
    localStorage.removeItem('user_role')
  }
})
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 240px minmax(0, 1fr);
  background: var(--color-bg);
}

.topbar {
  display: flex;
  align-items: stretch;
  flex-direction: column;
  justify-content: flex-start;
  gap: var(--space-4);
  min-height: 100vh;
  padding: var(--space-4) var(--space-3);
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  z-index: var(--z-topbar, 200);
}

.topbar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 0 var(--space-2);
}

.topbar-logo {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-xs);
  background: var(--color-primary);
  color: #ffffff;
  font-size: var(--text-sm);
  font-weight: 600;
}

.topbar-title {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.topbar-name {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text);
  line-height: 1.3;
}

.topbar-tag {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-weight: 400;
}

.top-nav {
  display: flex; flex-direction: column; align-items: stretch; gap: 1px; width: 100%; flex: 1; overflow-y: auto;
}

.top-nav::-webkit-scrollbar {
  display: none;
}

.nav-divider {
  width: auto; height: 1px; margin: 8px var(--space-2);
  background: var(--color-border);
  flex: 0 0 auto;
}

.nav-group-label {
  padding: 18px var(--space-2) 6px;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 500;
}

.nav-entry {
  height: 34px; justify-content: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 var(--space-2);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--text-base);
  font-weight: 400;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.nav-entry:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

/* 选中态用浅底 + 主色文字，不用渐变和光晕 */
.nav-entry.active {
  color: var(--color-primary);
  background: var(--color-primary-light);
  font-weight: 500;
}

.nav-entry .el-icon {
  font-size: 16px;
  color: currentColor;
}

/* 侧栏底部：一行工具入口 + 一行账号信息，不再是一叠圆角胶囊 */
.sidebar-footer {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.utility-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 0 var(--space-2) var(--space-1);
}

.utility-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  cursor: pointer;
  transition: color var(--transition-fast);
}

.utility-link:hover {
  color: var(--color-text);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-success);
  flex-shrink: 0;
}

.guide-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-primary);
  flex-shrink: 0;
}

.account-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: var(--space-2);
  border-radius: var(--radius-sm);
}

.account-avatar {
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
}

.account-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.account-name {
  font-size: var(--text-sm);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.account-role {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.logout-btn {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: 0;
  padding: 2px 4px;
  cursor: pointer;
  transition: color var(--transition-fast);
}

.logout-btn:hover {
  color: var(--color-danger);
}

.section-strip {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-6);
  max-width: 1500px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-8) var(--space-4);
}

.section-heading {
  min-width: 0;
}

.section-strip h1 {
  margin: 0 0 2px;
  color: var(--color-text);
  font-size: var(--text-xl);
  line-height: var(--text-xl-lh);
  font-weight: 600;
}

.section-strip p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.main-content {
  max-width: 1500px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-8) var(--space-10);
  background: transparent;
  min-height: calc(100vh - 180px);
}
.route-loading {
  display: grid;
  place-items: start;
  padding: var(--space-8) 0;
}
.route-loading-card {
  padding: var(--space-4) var(--space-6);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}
.app-workspace { min-width: 0; }

.content-stage {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
}

/* 路由切换只做淡入淡出，不做整页位移 */
.page-fade-enter-active {
  transition: opacity 0.16s ease;
}
.page-fade-leave-active {
  transition: opacity 0.1s ease;
}
.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}

@media (max-width: 1280px) {
  .app-shell { grid-template-columns: 68px minmax(0, 1fr); }
  .topbar-title, .nav-entry span, .utility-label, .account-meta, .nav-group-label { display: none; }
  .topbar { align-items: center; }
  .topbar-brand { justify-content: center; padding: 0; }
  .nav-entry { justify-content: center; padding: 0; }
  .utility-row, .account-row { flex-direction: column; justify-content: center; gap: 6px; }
}

@media (max-width: 760px) {
  .app-shell { display: block; }
  .topbar {
    min-height: auto; height: 56px; position: sticky; flex-direction: row;
    align-items: center; gap: var(--space-3); padding: 8px 16px;
    border-right: 0; border-bottom: 1px solid var(--color-border);
  }
  .top-nav { display: none; }
  .sidebar-footer {
    flex-direction: row; align-items: center; gap: var(--space-2);
    margin-left: auto; padding-top: 0; border-top: 0;
  }
  .utility-row, .account-row { flex-direction: row; padding: 0; }

  .section-strip {
    align-items: flex-start;
    flex-direction: column;
    padding: var(--space-4) var(--space-4) var(--space-3);
  }

  .main-content {
    padding: var(--space-3) var(--space-4) var(--space-8);
  }

  .section-strip h1 {
    font-size: var(--text-lg);
    line-height: var(--text-lg-lh);
  }
}

.mobile-nav {
  display: none;
}

@media (max-width: 760px) {
  .mobile-nav {
    display: flex;
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    height: 56px;
    align-items: center;
    justify-content: space-around;
    background: var(--color-surface);
    border-top: 1px solid var(--color-border);
    z-index: var(--z-topbar, 200);
    padding-bottom: env(safe-area-inset-bottom);
  }

  .mobile-nav-item {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 6px 2px;
    border: 0;
    background: transparent;
    color: var(--color-text-muted);
    font-size: 10px;
    font-weight: 400;
    cursor: pointer;
    transition: color var(--transition-fast);
  }

  .mobile-nav-item.active {
    color: var(--color-primary);
  }

  .main-content {
    padding-bottom: 72px;
  }
}
</style>
