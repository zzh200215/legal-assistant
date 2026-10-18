<template>
  <div class="management-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">平台设置与运营</h1>
        <p class="app-page-sub">管理组织、质量、策略和平台运行状态。这里的设置会影响整个团队。</p>
      </div>
      <div class="app-page-head-actions">
        <span class="app-tag is-info">仅管理员可见</span>
      </div>
    </header>

    <div class="management-sections">
      <section v-for="section in sections" :key="section.title" class="app-card">
        <div class="app-card-head">
          <h2>{{ section.title }}</h2>
          <span class="app-card-head-meta">{{ section.description }}</span>
        </div>
        <div class="app-card-body">
          <div class="management-links">
            <button v-for="item in section.items" :key="item.label" type="button" class="management-link" @click="go(item.path)">
              <span class="management-link-copy">
                <strong>{{ item.label }}</strong>
                <small>{{ item.description }}</small>
              </span>
              <span class="management-link-arrow" aria-hidden="true">›</span>
            </button>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
const sections = [
  {
    title: '平台运行',
    description: '服务状态、组织与访问控制',
    items: [
      { label: '系统', description: '健康检查、组织架构、权限和通知策略', path: '/system' },
      { label: '组织', description: '成员、部门和团队协作范围', path: '/system?tab=orgs' },
      { label: '计费', description: '订阅、配额和账单状态', path: '/pricing' },
    ],
  },
  {
    title: '自动化与质量',
    description: '运行记录、审批与质量观测',
    items: [
      { label: '自动化运行', description: '任务执行、人工审批和运行记录', path: '/agent' },
      { label: '工作流运行', description: '案件处理进度、失败和恢复状态', path: '/system?tab=tasks' },
      { label: '评测与审计', description: '质量评测、实验观测和操作日志', path: '/system?tab=experiments' },
      { label: '策略与治理', description: '敏感治理、工具健康和平台策略', path: '/system?tab=sensitivity' },
    ],
  },
]

const go = (path) => router.push(path)
</script>

<style scoped>
/* 页头与卡片骨架走 design-tokens.css 的 .app-page-head / .app-card，这里只排布入口列表 */
.management-page { display: grid; gap: var(--space-6); max-width: 1060px; padding-bottom: var(--space-8); }
.management-sections { display: grid; gap: var(--space-5); }
.management-links { display: grid; gap: 2px; }
.management-link { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); min-height: 64px; padding: var(--space-3) var(--space-3); border: 0; border-radius: var(--radius-md); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.management-link:hover { background: var(--color-surface-hover); }
.management-link-copy { display: grid; gap: 4px; min-width: 0; }
.management-link-copy strong { font-size: var(--text-sm); font-weight: 600; }
.management-link-copy small { color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.6; }
.management-link-arrow { flex: 0 0 auto; color: var(--color-primary); font-size: var(--text-2xl); line-height: 1; }
@media (max-width: 600px) {
  .app-page-head-actions { width: 100%; }
}
</style>
