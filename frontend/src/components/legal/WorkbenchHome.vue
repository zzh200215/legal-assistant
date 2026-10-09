<template>
  <section class="workbench-home">
    <header class="workbench-heading">
      <div>
        <p class="workbench-kicker">法律工作台</p>
        <h1>今天先处理什么？</h1>
        <p class="workbench-subtitle">从待审核事项、最近案件和关键动作继续工作。</p>
      </div>
      <button type="button" class="workspace-primary" :disabled="orgMissing" :title="orgMissing ? '你还未加入组织，暂无法创建案件' : undefined" @click="$emit('create-case')">新建案件</button>
    </header>

    <div v-if="orgMissing" class="workbench-org-alert" role="alert">
      <strong>你还未加入任何组织</strong>
      <p>案件、咨询与文书都以组织为单位工作，暂时无法创建内容。请联系系统管理员将你加入组织（或为你创建组织）后刷新本页。</p>
    </div>

    <section class="workbench-summary" aria-label="工作台摘要">
      <div><span>进行中案件</span><strong>{{ activeCaseCount }}</strong><button type="button" @click="$emit('open-cases')">查看案件</button></div>
      <div><span>待审核</span><strong>{{ reviewItems.length }}</strong><button type="button" @click="$emit('open-review')">打开审核</button></div>
      <div><span>法律咨询</span><strong>{{ overview?.counts?.consultations || 0 }}</strong><small>已归档咨询</small></div>
      <div><span>合同审查</span><strong>{{ overview?.counts?.contract_reviews || 0 }}</strong><small>已归档审查</small></div>
    </section>

    <div class="workbench-grid">
      <section class="workbench-section workbench-review">
        <div class="workbench-section-head"><div><h2>待处理审核</h2><p>需要律师作出决定的工作项</p></div><button type="button" @click="$emit('open-review')">查看全部</button></div>
        <div v-if="reviewLoading" class="workbench-empty">正在加载审核事项…</div>
        <div v-else-if="reviewItems.length" class="workbench-list">
          <button v-for="item in reviewItems.slice(0, 6)" :key="`${item.target_type}-${item.id}`" type="button" class="review-row" @click="$emit('open-review')">
            <span class="review-type">{{ reviewTypeLabel(item.target_type) }}</span>
            <span class="review-main"><strong>{{ item.title || item.question || '待审核记录' }}</strong><small>{{ item.case_title || '未关联案件' }} · {{ statusLabel(item.status) }}</small></span>
            <span class="review-date">{{ formatDate(item.created_at) }}</span>
          </button>
        </div>
        <div v-else class="workbench-empty">当前没有待审核事项。</div>
      </section>

      <section class="workbench-section workbench-cases">
        <div class="workbench-section-head"><div><h2>最近案件</h2><p>按最近更新排列</p></div><button type="button" @click="$emit('open-cases')">全部案件</button></div>
        <div v-if="cases.length" class="workbench-list">
          <button v-for="matter in recentCases" :key="matter.id" type="button" class="case-row" @click="$emit('open-case', matter)">
            <span class="case-status-mark" :class="`status-${matter.status}`"></span>
            <span class="case-main"><strong>{{ matter.title }}</strong><small>{{ matter.client_name || '未登记客户' }} · {{ caseStatusLabel(matter.status) }}</small></span>
            <span class="case-count">{{ caseRecordCount(matter) }} 项</span>
          </button>
        </div>
        <div v-else class="workbench-empty">还没有案件，先创建一个案件开始归档工作。</div>
      </section>
    </div>

    <section class="workbench-actions">
      <div class="workbench-section-head"><div><h2>常用入口</h2><p>直接进入下一项法律工作</p></div></div>
      <div class="action-list">
        <button type="button" @click="$emit('open-documents')"><strong>文档</strong><span>查看合同、证据和案件材料</span></button>
        <button type="button" @click="$emit('open-research')"><strong>法律研究</strong><span>查找法规、案例和业务模板</span></button>
        <button type="button" @click="$emit('open-tasks')"><strong>任务</strong><span>跟进案件协作和处理进度</span></button>
        <button type="button" @click="$emit('open-chat')"><strong>案件助手</strong><span>基于案件资料继续提问</span></button>
      </div>
    </section>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import legalWorkspace from '../../api/legalWorkspace'

defineEmits(['create-case', 'open-cases', 'open-review', 'open-case', 'open-documents', 'open-research', 'open-tasks', 'open-chat'])
const props = defineProps({ overview: { type: Object, default: null }, cases: { type: Array, default: () => [] }, orgMissing: { type: Boolean, default: false } })
const reviewItems = ref([])
const reviewLoading = ref(false)
const activeCaseCount = computed(() => props.cases.filter((item) => item.status === 'in_progress').length)
const recentCases = computed(() => [...props.cases].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || ''))).slice(0, 6))
const reviewTypeLabel = (type) => ({ consultation: '咨询', contract_review: '审查', draft: '文书' }[type] || '审核')
const statusLabel = (status) => ({ pending_review: '待审核', needs_lawyer_review: '需律师审核', needs_facts: '待补充事实' }[status] || status || '处理中')
const caseStatusLabel = (status) => ({ in_progress: '进行中', closed: '已结案', archived: '已归档' }[status] || status || '处理中')
const caseRecordCount = (matter) => (matter.item_counts?.consultations || 0) + (matter.item_counts?.reviews || 0) + (matter.item_counts?.drafts || 0)
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

onMounted(async () => {
  reviewLoading.value = true
  try {
    const { data } = await legalWorkspace.listLegalReviewQueue()
    reviewItems.value = data || []
  } catch {
    reviewItems.value = []
  } finally {
    reviewLoading.value = false
  }
})
</script>

<style scoped>
.workbench-home { display: grid; gap: 28px; padding: 8px 0 36px; }
.workbench-heading { display: flex; align-items: end; justify-content: space-between; gap: 24px; padding-bottom: 22px; border-bottom: 1px solid var(--color-border); }
.workbench-org-alert { display: grid; gap: 6px; margin-top: 20px; padding: 14px 16px; border: 1px solid var(--color-border); border-left: 3px solid var(--color-primary); border-radius: 6px; background: #F7FAFC; }
.workbench-org-alert strong { color: var(--color-text); font-size: 13px; font-weight: 600; }
.workbench-org-alert p { margin: 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.7; }
.workbench-kicker { margin: 0 0 7px; color: var(--color-primary); font-size: 12px; font-weight: 600; }
.workbench-heading h1 { margin: 0; color: var(--color-text); font-size: clamp(26px, 3vw, 36px); font-weight: 620; letter-spacing: 0; }
.workbench-subtitle { margin: 8px 0 0; color: var(--color-text-secondary); font-size: 14px; }
.workbench-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border-bottom: 1px solid var(--color-border); }
.workbench-summary > div { display: grid; gap: 5px; min-height: 96px; padding: 0 22px 18px 0; border-right: 1px solid var(--color-border-light); }
.workbench-summary > div + div { padding-left: 22px; }
.workbench-summary > div:last-child { border-right: 0; }
.workbench-summary span, .workbench-summary small { color: var(--color-text-muted); font-size: 12px; }
.workbench-summary strong { color: var(--color-text); font-size: 26px; line-height: 1; font-weight: 620; }
.workbench-summary button { width: max-content; padding: 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.workbench-grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 38px; }
.workbench-section, .workbench-actions { min-width: 0; }
.workbench-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--color-border); }
.workbench-section-head h2 { margin: 0; color: var(--color-text); font-size: 16px; font-weight: 620; }
.workbench-section-head p { margin: 4px 0 0; color: var(--color-text-muted); font-size: 12px; }
.workbench-section-head > button { flex: 0 0 auto; padding: 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.workbench-list { display: grid; }
.review-row, .case-row { display: grid; align-items: center; width: 100%; min-height: 62px; padding: 11px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.review-row { grid-template-columns: 52px minmax(0, 1fr) auto; gap: 12px; }
.case-row { grid-template-columns: 8px minmax(0, 1fr) auto; gap: 12px; }
.review-row:hover, .case-row:hover, .action-list button:hover { background: #F8FAFB; }
.review-type, .review-date, .case-count { color: var(--color-text-muted); font-size: 11px; }
.review-main, .case-main { display: grid; gap: 4px; min-width: 0; }
.review-main strong, .case-main strong { overflow: hidden; color: var(--color-text); font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.review-main small, .case-main small { color: var(--color-text-muted); font-size: 11px; }
.case-status-mark { width: 6px; height: 30px; background: var(--color-border); }
.case-status-mark.status-in_progress { background: var(--color-primary); }
.case-status-mark.status-closed { background: #78A889; }
.case-status-mark.status-archived { background: #A6AFB8; }
.workbench-empty { padding: 28px 4px; color: var(--color-text-muted); font-size: 13px; }
.workbench-actions { padding-top: 8px; }
.action-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); }
.action-list button { display: grid; gap: 7px; min-height: 92px; padding: 16px 18px 16px 0; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.action-list button + button { padding-left: 18px; border-left: 1px solid var(--color-border-light); }
.action-list strong { font-size: 14px; font-weight: 600; }
.action-list span { color: var(--color-text-muted); font-size: 12px; line-height: 1.5; }
@media (max-width: 900px) { .workbench-grid { grid-template-columns: 1fr; gap: 28px; } .action-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } .action-list button:nth-child(3) { padding-left: 0; border-left: 0; } }
@media (max-width: 640px) { .workbench-heading { align-items: flex-start; flex-direction: column; } .workbench-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px 0; } .workbench-summary > div:nth-child(2) { border-right: 0; } .workbench-summary > div:nth-child(3) { padding-left: 0; } .review-row { grid-template-columns: 44px minmax(0, 1fr); } .review-date { grid-column: 2; } .action-list { grid-template-columns: 1fr; } .action-list button + button { padding-left: 0; border-left: 0; } }
</style>
