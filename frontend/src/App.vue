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
          <span class="topbar-tag">法律文书与合同审查工作台</span>
        </div>
      </div>

      <nav class="top-nav" aria-label="主导航">
        <button
          v-for="item in visibleNavItems(navItems)"
          :key="item.path"
          class="nav-entry"
          :class="isRouteActive(item.path) ? 'active' : ''"
          :title="item.caption"
          @click="onMenuSelect(item.path)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>

        <template v-for="group in visibleNavGroups" :key="group.label">
          <span class="nav-divider" aria-hidden="true"></span>
          <button
            v-for="item in group.items"
            :key="item.path"
            class="nav-entry"
            :class="isRouteActive(item.path) ? 'active' : ''"
            :title="`${group.label} · ${item.caption}`"
            @click="onMenuSelect(item.path)"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </button>
        </template>
      </nav>

      <div class="topbar-actions">
        <span class="workspace-pill">法律知识空间</span>
        <span v-if="user?.role" class="role-badge">{{ user.role === 'admin' ? '管理员' : '成员' }}</span>
        <NotificationBell />
        <button class="status-pill" @click="onMenuSelect('/system')">
          <span class="status-dot"></span>
          平台状态
        </button>
        <div class="user-pill">
          <span class="user-avatar">{{ (user?.username || 'AI').slice(0, 1).toUpperCase() }}</span>
          <span class="user-name">{{ user?.username || '未登录用户' }}</span>
          <button class="logout-btn" @click="logout">退出</button>
        </div>
      </div>
    </aside>

    <div class="app-workspace">
    <section class="section-strip">
      <div>
        <span class="section-kicker">Workspace</span>
        <h1>{{ currentSection.label }}</h1>
        <p>{{ currentSection.description }}</p>
      </div>
      <button class="section-cta" @click="onMenuSelect('/legal-workspace')">法律咨询</button>
    </section>

    <main class="main-content">
      <router-view v-slot="{ Component }">
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
        :class="isRouteActive(item.path) ? 'active' : ''"
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
import {
  ChatDotRound,
  Check,
  Cpu,
  DataLine,
  Document,
  House,
  Notebook,
  ScaleToOriginal,
} from '@element-plus/icons-vue'
import api from './api'
import NotificationBell from './components/legal/NotificationBell.vue'

const route = useRoute()
const router = useRouter()
const user = ref(null)
const isAdmin = computed(() => user.value?.role === 'admin')
const isPublicRoute = computed(() => route.meta?.public === true)

const ScaleIcon = ScaleToOriginal

const navItems = [
  { path: '/legal-workspace', label: '法律工作台', caption: '咨询、审查、文书与审核', icon: ScaleIcon },
  { path: '/pricing', label: '订阅方案', caption: '套餐、配额与购买', icon: ScaleIcon },
  { path: '/documents', label: '法律知识库', caption: '法规、案例、合同模板与文书模板', icon: Document },
  { path: '/chat', label: '对话记录', caption: '通用对话与流式输出', icon: ChatDotRound },
]

const navGroups = [
  {
    label: '法律业务',
    items: [
      { path: '/tasks', label: '待办任务', caption: '执行项与协作推进', icon: Check, adminOnly: true },
      { path: '/agent', label: 'Agent配置', caption: '工具编排与执行观测', icon: Cpu, adminOnly: true },
    ],
  },
  {
    label: '平台管理',
    items: [
      { path: '/system', label: '系统中心', caption: '观测、连接器与任务中心', icon: DataLine, adminOnly: true },
    ],
  },
]

const sectionMeta = {
  '/legal-workspace': { label: '法律工作台', description: '法律咨询、合同审查、文书草稿与律师审核' },
  '/': { label: '法律工作台', description: '法律咨询、合同审查、文书草稿与律师审核' },
  '/pricing': { label: '订阅方案', description: '套餐选择、配额说明与订阅管理' },
  '/documents': { label: '法律知识库', description: '法规、案例、合同模板与文书模板的入库、解析与检索' },
  '/tasks': { label: '待办任务', description: '管理执行项、协作进度和任务来源' },
  '/agent': { label: 'Agent配置', description: '配置 Agent 目标、工具调用和执行追踪' },
  '/chat': { label: '对话记录', description: '查看流式对话、上下文消息和引用材料' },
  '/system': { label: '系统中心', description: '查看平台健康、连接器和全链路观测' },
}

const currentSection = computed(() => sectionMeta[route.path] || { label: '律智检', description: '法律文书与合同审查工作台' })
const visibleNavGroups = computed(() =>
  navGroups
    .map((group) => ({ ...group, items: visibleNavItems(group.items) }))
    .filter((group) => group.items.length)
)

const visibleNavItems = (items) => items.filter((item) => !item.adminOnly || isAdmin.value)
const mobileNavItems = computed(() =>
  visibleNavItems([...navItems, ...navGroups.flatMap((group) => group.items)]),
)
const isRouteActive = (path) => route.path === path
const onMenuSelect = (path) => router.push(path)

const logout = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('user_role')
  router.push('/login')
}

onMounted(async () => {
  if (isPublicRoute.value) return
  try {
    const { data } = await api.getMe()
    user.value = data
    localStorage.setItem('user_role', data.role || 'user')
  } catch {
    user.value = null
    localStorage.removeItem('user_role')
  }
})
</script>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  background:
    radial-gradient(circle at 18% 0%, rgba(79, 106, 245, 0.12), transparent 34%),
    linear-gradient(180deg, #FFFFFF 0%, var(--color-bg) 34%, #FFFFFF 100%);
}

.topbar {
  display: flex;
  align-items: center;
  flex-direction: column;
  justify-content: flex-start;
  gap: var(--space-5);
  min-height: 100vh;
  padding: var(--space-5) var(--space-3);
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  border-bottom: 1px solid var(--color-border-light);
  position: sticky;
  top: 0;
  z-index: var(--z-topbar, 200);
  box-shadow: 0 8px 24px rgba(79, 106, 245, 0.05);
}

.topbar-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 188px;
}

.topbar-logo {
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--gradient-brand);
  color: #ffffff;
  font-size: var(--text-sm);
  font-weight: 800;
  box-shadow: 0 10px 24px rgba(79, 106, 245, 0.28);
}

.topbar-title {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.topbar-name {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--color-text);
  line-height: 1.2;
}

.topbar-tag {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-weight: 500;
}

.top-nav {
  display: flex; flex-direction: column; align-items: stretch; gap: 4px; width: 100%; flex: 1; overflow-y: auto;
}

.top-nav::-webkit-scrollbar {
  display: none;
}

.nav-divider {
  width: 100%; height: 1px; margin: 8px 0;
  background: var(--color-border);
  flex: 0 0 auto;
}

.nav-entry {
  height: 40px; justify-content: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  transition: transform var(--transition-fast), background var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast), box-shadow var(--transition-fast);
}

.nav-entry:hover {
  color: var(--color-primary);
  background: var(--color-primary-light);
  border-color: rgba(79, 106, 245, 0.12);
  transform: translateY(-1px);
}

.nav-entry.active {
  color: #ffffff;
  background: var(--gradient-brand);
  border-color: transparent;
  box-shadow: 0 8px 20px rgba(79, 106, 245, 0.22);
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto; width: 100%; flex-direction: column; align-items: stretch;
}

.workspace-pill,
.role-badge,
.status-pill,
.user-pill {
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius-full);
  border: 1px solid var(--color-border-light);
  background: #ffffff;
  box-shadow: var(--shadow-xs);
  padding: 0 12px;
  font-size: var(--text-xs);
  font-weight: 700;
  color: var(--color-text-secondary);
}

.role-badge {
  color: var(--color-primary);
  background: var(--color-primary-light);
  border-color: transparent;
}

.status-pill {
  gap: 6px;
  cursor: pointer;
  transition: transform var(--transition-fast), box-shadow var(--transition-fast);
}

.status-pill:hover {
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-success);
  box-shadow: 0 0 0 4px rgba(34, 197, 94, 0.14);
}

.user-pill {
  gap: 8px;
  padding: 3px 4px 3px 6px;
}

.user-avatar {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  background: var(--gradient-success);
  color: #ffffff;
  font-size: 11px;
  font-weight: 800;
}

.user-name {
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logout-btn {
  font-size: var(--text-xs);
  font-weight: 700;
  color: var(--color-text-secondary);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  padding: 4px 9px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.logout-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-light);
}

.section-strip {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-6);
  max-width: 1500px;
  margin: 0 auto;
  padding: var(--space-8) var(--space-8) var(--space-4);
}

.section-kicker {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: var(--radius-full);
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 800;
}

.section-strip h1 {
  margin: 10px 0 4px;
  color: var(--color-text);
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  font-weight: 800;
}

.section-strip p {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.section-cta {
  flex: 0 0 auto;
  min-width: 120px;
  height: 40px;
  border: 0;
  border-radius: var(--radius-full);
  background: var(--gradient-brand);
  color: #ffffff;
  font-size: var(--text-sm);
  font-weight: 800;
  cursor: pointer;
  transition: all var(--transition-fast);
  box-shadow: 0 12px 24px rgba(79, 106, 245, 0.24);
}

.section-cta:hover {
  transform: translateY(-1px);
  box-shadow: 0 16px 30px rgba(79, 106, 245, 0.30);
}

.main-content {
  max-width: 1500px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-8) var(--space-10);
  background: transparent;
  min-height: calc(100vh - 180px);
}
.app-workspace { min-width: 0; }

.content-stage {
  width: 100%;
  max-width: 1600px;
  margin: 0 auto;
}

.page-fade-enter-active {
  transition: opacity 0.25s cubic-bezier(0.4, 0, 0.2, 1),
              transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.page-fade-leave-active {
  transition: opacity 0.15s cubic-bezier(0.4, 0, 0.2, 1),
              transform 0.15s cubic-bezier(0.4, 0, 0.2, 1);
}
.page-fade-enter-from {
  opacity: 0;
  transform: translateY(12px);
}
.page-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

@media (max-width: 1280px) {
  .app-shell { grid-template-columns: 72px minmax(0, 1fr); }
  .topbar-title, .nav-entry span, .topbar-actions { display: none; }
  .topbar { align-items: center; }
  .nav-entry { justify-content: center; padding: 0; }
}

@media (max-width: 760px) {
  .app-shell { display: block; }
  .topbar { min-height: auto; height: 58px; position: sticky; flex-direction: row; padding: 8px 16px; }
  .top-nav { display: none; }
  .workspace-pill,
  .role-badge,
  .user-name {
    display: none;
  }

  .section-strip {
    align-items: flex-start;
    flex-direction: column;
    padding: var(--space-6) var(--space-4) var(--space-3);
  }

  .main-content {
    padding: var(--space-3) var(--space-4) var(--space-8);
  }

  .section-strip h1 {
    font-size: var(--text-2xl);
    line-height: var(--text-2xl-lh);
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
    background: rgba(255, 255, 255, 0.96);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-top: 1px solid var(--color-border-light);
    z-index: var(--z-topbar, 200);
    padding-bottom: env(safe-area-inset-bottom);
  }

  .mobile-nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    padding: 6px 14px;
    border: 0;
    background: transparent;
    color: var(--color-text-secondary);
    font-size: var(--text-xs);
    font-weight: 600;
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
