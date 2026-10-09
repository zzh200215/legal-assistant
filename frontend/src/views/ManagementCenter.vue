<template>
  <div class="management-page">
    <header class="management-heading">
      <div>
        <p class="management-kicker">管理中心</p>
        <h1>平台设置与运营</h1>
        <p>管理组织、质量、策略和平台运行状态。这里的设置会影响整个团队。</p>
      </div>
      <span class="management-access">仅管理员可见</span>
    </header>

    <div class="management-sections">
      <section v-for="section in sections" :key="section.title" class="management-section">
        <div class="management-section-heading">
          <h2>{{ section.title }}</h2>
          <span>{{ section.description }}</span>
        </div>
        <div class="management-links">
          <button v-for="item in section.items" :key="item.label" type="button" class="management-link" @click="go(item.path)">
            <span class="management-link-copy">
              <strong>{{ item.label }}</strong>
              <small>{{ item.description }}</small>
            </span>
            <span class="management-link-arrow" aria-hidden="true">›</span>
          </button>
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
.management-page { display: grid; gap: 30px; max-width: 1060px; }
.management-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding-bottom: 22px; border-bottom: 1px solid var(--color-border); }
.management-kicker { margin: 0 0 7px; color: var(--color-primary); font-size: 11px; font-weight: 650; }
.management-heading h1 { margin: 0; color: var(--color-text); font-size: 24px; line-height: 1.35; font-weight: 620; }
.management-heading p:not(.management-kicker) { max-width: 620px; margin: 7px 0 0; color: var(--color-text-secondary); font-size: 13px; line-height: 1.65; }
.management-access { flex: 0 0 auto; padding-top: 4px; color: var(--color-text-muted); font-size: 11px; }
.management-sections { display: grid; gap: 32px; }
.management-section { display: grid; gap: 12px; }
.management-section-heading { display: flex; align-items: baseline; gap: 12px; }
.management-section-heading h2 { margin: 0; color: var(--color-text); font-size: 15px; font-weight: 620; }
.management-section-heading span { color: var(--color-text-muted); font-size: 12px; }
.management-links { display: grid; border-top: 1px solid var(--color-border); }
.management-link { display: flex; align-items: center; justify-content: space-between; gap: 18px; min-height: 70px; padding: 13px 6px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.management-link:hover { background: var(--color-surface-hover); }
.management-link-copy { display: grid; gap: 4px; }
.management-link-copy strong { font-size: 13px; font-weight: 600; }
.management-link-copy small { color: var(--color-text-muted); font-size: 12px; line-height: 1.55; }
.management-link-arrow { color: var(--color-primary); font-size: 22px; line-height: 1; }
@media (max-width: 600px) { .management-heading { flex-direction: column; gap: 8px; } .management-heading h1 { font-size: 21px; } .management-section-heading { align-items: flex-start; flex-direction: column; gap: 4px; } }
</style>
