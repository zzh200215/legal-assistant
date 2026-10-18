<template>
  <section class="workbench-home">
    <!-- 方向 B 页头：28px 标题 + 说明，动作区右对齐（替代原“双 h1 + 下划线”版式） -->
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">今天先处理什么？</h1>
        <p class="app-page-sub">从待审核事项、最近案件和关键动作继续工作。</p>
      </div>
      <div class="app-page-head-actions">
        <button type="button" class="workspace-primary" :disabled="orgMissing" :title="orgMissing ? '你还未加入组织，暂无法创建案件' : undefined" @click="$emit('create-case')">新建案件</button>
      </div>
    </header>

    <div v-if="orgMissing" class="workbench-alert is-warning" role="alert">
      <strong>你还未加入任何组织</strong>
      <p>案件、咨询与文书都以组织为单位工作，暂时无法创建内容。请联系系统管理员将你加入组织（或为你创建组织）后刷新本页。</p>
    </div>

    <!-- 四项摘要通栏铺开：主栏只有 ~420px，塞进两栏里会把 small 文案挤断 -->
    <div class="app-metric-grid-4">
      <div class="app-stat">
        <span class="app-stat-label">进行中案件</span>
        <div class="app-stat-value">{{ activeCaseCount }}<small>共 {{ cases.length }} 件</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">待审核</span>
        <div class="app-stat-value">{{ reviewItems.length }}<small>项待决定</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">法律咨询</span>
        <div class="app-stat-value">{{ overview?.counts?.consultations || 0 }}<small>已归档</small></div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">合同审查</span>
        <div class="app-stat-value">{{ overview?.counts?.contract_reviews || 0 }}<small>已归档</small></div>
      </div>
    </div>

    <div class="app-workspace-body">
      <div class="workbench-main">
        <section class="app-card workbench-review">
          <div class="app-card-head">
            <span>待处理审核</span>
            <span class="app-card-head-meta">{{ reviewLoading ? '加载中' : `${reviewItems.length} 项待决定` }}</span>
            <button type="button" class="card-action" @click="$emit('open-review')">查看全部</button>
          </div>
          <div class="app-card-body">
            <!-- 三态区分（ux-audit P1-3/M-13）：加载失败不再是"空数据"假象 -->
            <QueryStateView :status="reviewStatus" :error="reviewError" min-height="0px" @retry="loadReviewItems">
              <template #empty><p class="workbench-empty">当前没有待审核事项。</p></template>
              <div class="thread-stack">
                <button v-for="item in reviewItems.slice(0, 6)" :key="`${item.target_type}-${item.id}`" type="button" class="app-thread" @click="$emit('open-review')">
                  <span class="app-thread-title">{{ item.title || item.question || '待审核记录' }}</span>
                  <span class="app-thread-meta">
                    <span class="app-tag is-info">{{ reviewTypeLabel(item.target_type) }}</span>
                    <span class="thread-snippet">{{ item.case_title || '未关联案件' }} · {{ statusLabel(item.status) }}</span>
                    <span class="thread-time">{{ formatDate(item.created_at) }}</span>
                  </span>
                </button>
              </div>
            </QueryStateView>
          </div>
        </section>

        <section class="app-card workbench-cases">
          <div class="app-card-head">
            <span>最近案件</span>
            <span class="app-card-head-meta">{{ casesLoading ? '加载中' : `${recentCases.length} 个` }}</span>
            <button type="button" class="card-action" @click="$emit('open-cases')">全部案件</button>
          </div>
          <div class="app-card-body">
            <QueryStateView :status="casesStatus" :error="casesError" min-height="0px" @retry="$emit('retry-cases')">
              <template #empty>
                <div class="workbench-empty">
                  <p>还没有案件，先创建一个案件开始归档工作。</p>
                  <button type="button" class="workspace-primary is-compact" :disabled="orgMissing" @click="$emit('create-case')">新建案件</button>
                </div>
              </template>
              <div class="thread-stack">
                <button v-for="matter in recentCases" :key="matter.id" type="button" class="app-thread" @click="$emit('open-case', matter)">
                  <span class="app-thread-title">{{ matter.title }}</span>
                  <span class="app-thread-meta">
                    <span class="app-tag" :class="caseStatusClass(matter.status)">{{ caseStatusLabel(matter.status) }}</span>
                    <span class="thread-snippet">{{ matter.client_name || '未登记客户' }} · {{ caseRecordCount(matter) }} 项工作</span>
                    <span class="thread-time">{{ formatDate(matter.updated_at) }}</span>
                  </span>
                </button>
              </div>
            </QueryStateView>
          </div>
        </section>
      </div>

      <aside class="app-rail">
        <section class="app-card">
          <div class="app-card-head"><span>常用入口</span></div>
          <div class="app-card-body rail-actions">
            <button type="button" class="app-thread" @click="$emit('open-documents')">
              <span class="app-thread-title">文档</span>
              <span class="app-thread-meta">查看合同、证据和案件材料</span>
            </button>
            <button type="button" class="app-thread" @click="$emit('open-research')">
              <span class="app-thread-title">法律研究</span>
              <span class="app-thread-meta">查找法规、案例和业务模板</span>
            </button>
            <button type="button" class="app-thread" @click="$emit('open-tasks')">
              <span class="app-thread-title">任务</span>
              <span class="app-thread-meta">跟进案件协作和处理进度</span>
            </button>
            <button type="button" class="app-thread" @click="$emit('open-chat')">
              <span class="app-thread-title">案件助手</span>
              <span class="app-thread-meta">基于案件资料继续提问</span>
            </button>
          </div>
        </section>

        <section v-if="!authStore.currentUser?.onboarded_at" class="app-card">
          <div class="app-card-head"><span>刚开始使用？完成三步上手</span></div>
          <div class="app-card-body">
            <p class="rail-note">选择你的角色，按引导创建第一个案件。</p>
            <button type="button" class="card-action" @click="router.push('/legal-onboarding')">开始使用引导</button>
          </div>
        </section>
      </aside>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import legalWorkspace from '../../api/legalWorkspace'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const router = useRouter()
const authStore = useAuthStore()

defineEmits(['create-case', 'open-cases', 'open-review', 'open-case', 'open-documents', 'open-research', 'open-tasks', 'open-chat', 'retry-cases'])
const props = defineProps({
  overview: { type: Object, default: null },
  cases: { type: Array, default: () => [] },
  orgMissing: { type: Boolean, default: false },
  // 案件查询三态（父级 useQuery 状态透传，ux-audit P1-3/M-13）
  casesLoading: { type: Boolean, default: false },
  casesError: { type: Object, default: null },
})
const reviewItems = ref([])
const reviewLoading = ref(false)
const reviewError = ref(null)
const activeCaseCount = computed(() => props.cases.filter((item) => item.status === 'in_progress').length)
const recentCases = computed(() => [...props.cases].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))).slice(0, 6))
const reviewTypeLabel = (type) => ({ consultation: '咨询', contract_review: '审查', draft: '文书' }[type] || '审核')
const statusLabel = (status) => ({ pending_review: '待审核', needs_lawyer_review: '需律师审核', needs_facts: '待补充事实' }[status] || status || '处理中')
const caseStatusLabel = (status) => ({ in_progress: '进行中', closed: '已结案', archived: '已归档' }[status] || status || '处理中')
// 标签配色：只有“进行中”用强调色，其余保持中性，避免一屏彩色标签
const caseStatusClass = (status) => (status === 'in_progress' ? 'is-accent' : '')
const caseRecordCount = (matter) => (matter.item_counts?.consultations || 0) + (matter.item_counts?.reviews || 0) + (matter.item_counts?.drafts || 0)
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

const reviewStatus = computed(() => {
  if (reviewLoading.value) return 'loading'
  if (reviewError.value) return 'error'
  if (!reviewItems.value.length) return 'empty'
  return 'success'
})
const casesStatus = computed(() => {
  if (props.casesLoading) return 'loading'
  if (props.casesError) return 'error'
  if (!props.cases.length) return 'empty'
  return 'success'
})

const loadReviewItems = async () => {
  reviewLoading.value = true
  reviewError.value = null
  try {
    const { data } = await legalWorkspace.listLegalReviewQueue()
    reviewItems.value = data || []
  } catch (error) {
    reviewItems.value = []
    reviewError.value = error
    console.error('[workbench] 待审核列表加载失败', error)
  } finally {
    reviewLoading.value = false
  }
}

onMounted(loadReviewItems)
</script>

<style scoped>
/* 版式骨架全部取自 design-tokens.css 的 .app-* 类；这里只补页面级排布与动作样式，颜色/间距/圆角一律走令牌 */
.workbench-home { display: grid; gap: var(--space-6); padding-bottom: var(--space-10); }
.workbench-main { display: grid; gap: var(--space-4); min-width: 0; }
.workbench-alert { display: grid; gap: 6px; padding: var(--space-4) var(--space-5); border: 1px solid var(--color-border); border-left: 3px solid var(--color-primary); border-radius: var(--radius-lg); background: var(--color-surface); }
.workbench-alert strong { color: var(--color-text); font-size: var(--text-base); font-weight: 600; }
.workbench-alert p { margin: 0; color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.7; }
.workbench-alert.is-warning { border-color: var(--color-warning-light); border-left-color: var(--color-warning); background: var(--color-warning-light); }

/* 主按钮 .workspace-primary 已在 design-tokens.css 里统一（全局类，本组件直接用） */

/* 卡片头右侧的文字动作（查看全部 / 全部案件） */
.card-action { flex: 0 0 auto; border: 0; padding: 0; background: none; color: var(--color-primary); font: inherit; font-size: var(--text-xs); font-weight: 600; cursor: pointer; }
.card-action:hover { color: var(--color-primary-hover); text-decoration: underline; }

/* 列表：卡片内可点击行，标题 + 元信息，与预览 .thread-item 同构 */
.thread-stack { display: grid; gap: var(--space-2); }
.thread-snippet { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.thread-time { margin-left: auto; flex: 0 0 auto; color: var(--color-text-muted); font-size: var(--text-xs); }

/* 三态空态：文案 + 可执行动作 */
.workbench-empty { display: grid; gap: var(--space-3); justify-items: start; margin: 0; padding: var(--space-2) 0; color: var(--color-text-muted); font-size: var(--text-sm); line-height: 1.7; }
.workbench-empty p { margin: 0; }

/* 右栏：常用入口 + 上手引导 */
.rail-actions { display: grid; gap: var(--space-2); }
.rail-note { margin: 0 0 var(--space-3); color: var(--color-text-secondary); font-size: var(--text-sm); line-height: 1.7; }

@media (max-width: 640px) {
  .app-page-head-actions { width: 100%; margin-left: 0; }
  .app-page-head-actions .workspace-primary { width: 100%; }
}
</style>
