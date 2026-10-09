<template>
  <div class="legal-page workspace-page">
    <template v-if="view === 'cases'">
      <header class="legal-page-heading">
        <div>
          <h1>案件</h1>
          <p>按案件集中查看工作进展、咨询、审查与文书。</p>
        </div>
        <button class="workspace-primary" type="button" :disabled="!hasOrg" :title="!hasOrg ? '你还未加入组织，暂无法创建案件' : undefined" @click="caseDialog?.open()">新建案件</button>
      </header>

      <div class="case-list-toolbar">
        <input v-model="caseSearch" aria-label="搜索案件" placeholder="搜索案件名称、客户或案情" />
        <span class="legal-muted">{{ filteredCases.length }} 个案件</span>
      </div>
      <div class="case-list">
        <button v-for="matter in filteredCases" :key="matter.id" class="case-list-row" type="button" @click="openMatter(matter)">
          <span class="case-list-main">
            <strong>{{ matter.title }}</strong>
            <span>{{ matter.client_name || '未登记客户' }}<template v-if="matter.opposing_party"> · {{ matter.opposing_party }}</template></span>
          </span>
          <span class="case-list-type">{{ categoryLabel(matter.case_type) }}</span>
          <span class="case-list-counts">
            咨询 {{ matter.item_counts?.consultations || 0 }} · 审查 {{ matter.item_counts?.reviews || 0 }} · 文书 {{ matter.item_counts?.drafts || 0 }}
          </span>
          <span class="case-list-status" :class="`status-${matter.status}`">{{ caseStatusLabel(matter.status) }}</span>
        </button>
        <div v-if="!filteredCases.length" class="legal-empty">{{ cases.length ? '没有符合条件的案件' : '还没有案件，创建一个案件以开始归档工作。' }}</div>
      </div>
    </template>

    <template v-else-if="view === 'research'">
      <header class="legal-page-heading">
        <div><h1>法律资料</h1><p>查看法规、案例与业务模板。</p></div>
      </header>
      <AsyncLegalSourcesTab />
    </template>

    <template v-else-if="view === 'review'">
      <header class="legal-page-heading">
        <div><h1>审核</h1><p>律师复核建议并作出最终处理决定。</p></div>
      </header>
      <LegalReviewTab ref="reviewTabRef" />
    </template>

    <template v-else-if="currentCase">
      <div class="matter-toolbar">
        <el-select v-model="currentCaseId" class="matter-select" filterable aria-label="切换案件" @change="handleCaseSelect">
          <el-option v-for="matter in cases" :key="matter.id" :label="matter.title" :value="matter.id" />
        </el-select>
        <button class="quiet-action" type="button" @click="goToView('cases')">全部案件</button>
        <button class="quiet-action new-matter" type="button" :disabled="!hasOrg" @click="caseDialog?.open()">新建案件</button>
      </div>

      <CaseHeader :matter="currentCase" @continue="continueMatter" />

      <nav class="legal-matter-tabs" aria-label="案件工作区">
        <button
          v-for="tab in matterTabs"
          :key="tab.key"
          class="legal-matter-tab"
          :class="{ active: activeCaseTab === tab.key }"
          type="button"
          :aria-current="activeCaseTab === tab.key ? 'page' : undefined"
          @click="setMatterTab(tab.key)"
        >{{ tab.label }}</button>
        <select class="matter-tools-select" :value="legacyToolValue" aria-label="更多案件工具" @change="setMatterTab($event.target.value)">
          <option value="">更多工具</option>
          <option value="billing">计时计费</option>
          <option value="contracts">合同台账</option>
          <option value="portal">客户门户</option>
        </select>
      </nav>

      <section v-if="activeCaseTab === 'overview'" class="matter-overview">
        <div class="matter-main-column">
          <section class="legal-section matter-summary-section">
            <div class="legal-section-heading"><h2>案情摘要</h2><span>{{ caseUpdatedLabel }}</span></div>
            <p v-if="currentCase.description" class="matter-summary">{{ currentCase.description }}</p>
            <p v-else class="legal-muted">尚未填写案情摘要。</p>
          </section>

          <section class="legal-section matter-activity-section">
            <div class="legal-section-heading"><h2>最近活动</h2><button class="text-action" type="button" @click="setMatterTab('tasks')">查看全部</button></div>
            <AsyncMatterActivity :items="caseActivities.slice(0, 6)" />
          </section>

          <section class="legal-section matter-materials-section">
            <div class="legal-section-heading"><h2>关键文档与成果</h2><button class="text-action" type="button" @click="setMatterTab('documents')">打开文档</button></div>
            <div v-if="keyMaterials.length" class="material-list">
              <button v-for="item in keyMaterials.slice(0, 5)" :key="`${item.type}-${item.id}`" class="material-row" type="button" @click="openRecord(item)">
                <span class="material-kind">{{ recordTypeLabel(item.type) }}</span>
                <strong>{{ item.title }}</strong>
                <span class="legal-muted">{{ caseStatusLabel(item.status) }}</span>
              </button>
            </div>
            <div v-else class="legal-empty inline-empty">还没有合同审查或文书成果。</div>
          </section>
        </div>

        <aside class="matter-side-column">
          <section class="legal-section pending-section">
            <div class="legal-section-heading"><h2>待处理事项</h2><span>{{ pendingItems.length }}</span></div>
            <div v-if="pendingItems.length" class="pending-list">
              <button v-for="item in pendingItems.slice(0, 5)" :key="`${item.type}-${item.id}`" class="pending-row" type="button" @click="openRecord(item)">
                <span class="pending-mark" :class="`mark-${item.risk_level || 'normal'}`"></span>
                <span><strong>{{ item.title }}</strong><small>{{ recordTypeLabel(item.type) }} · {{ caseStatusLabel(item.status) }}</small></span>
              </button>
            </div>
            <p v-else class="legal-muted pending-empty">目前没有待处理的咨询、审查或文书。</p>
          </section>

          <section class="legal-section case-count-section">
            <div class="legal-section-heading"><h2>案件工作流</h2></div>
            <div class="workflow-list">
              <button type="button" @click="setMatterTab('consultation')"><span>法律咨询</span><strong>{{ currentCase.item_counts?.consultations || 0 }}</strong></button>
              <button type="button" @click="setMatterTab('contract')"><span>合同审查</span><strong>{{ currentCase.item_counts?.reviews || 0 }}</strong></button>
              <button type="button" @click="setMatterTab('draft')"><span>文书</span><strong>{{ currentCase.item_counts?.drafts || 0 }}</strong></button>
              <button type="button" @click="setMatterTab('review')"><span>律师审核</span><strong>{{ pendingReviewCount }}</strong></button>
            </div>
          </section>

          <AsyncWorkflowRunsPanel
            :case-id="currentCaseId"
            title="执行进度"
            description="查看案件动作的处理进度。"
            :limit="5"
          />

          <section class="legal-section dates-section">
            <div class="legal-section-heading"><h2>关键日期</h2><button class="text-action" type="button" @click="setMatterTab('deadlines')">管理日期</button></div>
            <p class="legal-muted">在案件日程中记录开庭、答辩与履行期限。</p>
          </section>

          <section class="matter-assistant-strip">
            <div>
              <p class="assistant-kicker">案件助手</p>
              <strong>基于当前案件资料继续工作</strong>
              <span>分析案情、查找法源、准备文书，回答会保留在当前案件上下文中。</span>
            </div>
            <button type="button" class="text-action" @click="router.push({ path: '/chat', query: { case_id: currentCaseId } })">打开助手</button>
          </section>
        </aside>
      </section>

      <section v-else-if="activeCaseTab === 'documents'" class="matter-panel">
        <AsyncCaseDocumentsPanel :case-id="currentCaseId" />
        <AsyncLegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
        <div class="legal-section related-records">
          <div class="legal-section-heading"><h2>相关工作记录</h2></div>
          <div v-if="caseActivities.length" class="compact-record-list">
            <button v-for="item in caseActivities" :key="`${item.type}-${item.id}`" type="button" @click="openRecord(item)">
              <span>{{ recordTypeLabel(item.type) }}</span><strong>{{ item.title }}</strong><time>{{ formatDate(item.created_at) }}</time>
            </button>
          </div>
          <div v-else class="legal-empty">案件暂无工作记录。</div>
        </div>
      </section>

      <section v-else-if="activeCaseTab === 'consultation'" class="matter-panel">
        <div class="legal-section-heading"><h2>案件法律咨询</h2></div>
        <AsyncLegalConsultationsTab :key="`consultation-${currentCaseId}`" :case-id="currentCaseId" :on-review-submitted="refreshReviewQueue" @go-to-draft="handleGoToDraftFromConsult" @go-to-review="handleGoToReviewFromConsult" />
      </section>

      <section v-else-if="activeCaseTab === 'contract'" class="matter-panel">
        <LegalContractTab :key="`contract-${currentCaseId}`" ref="contractsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </section>

      <section v-else-if="activeCaseTab === 'draft'" class="matter-panel">
        <div class="legal-section-heading"><h2>案件文书</h2></div>
        <LegalDraftsTab :key="`draft-${currentCaseId}`" ref="draftsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </section>

      <section v-else-if="activeCaseTab === 'review'" class="matter-panel">
        <div class="legal-section-heading"><div><h2>律师审核</h2><span>审核律师对专业意见作最终决定。</span></div></div>
        <LegalReviewTab :key="`review-${currentCaseId}`" ref="reviewTabRef" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'tasks'" class="matter-panel">
        <div class="legal-section-heading"><div><h2>案件工作记录</h2><span>按时间查看咨询、合同审查与文书处理。</span></div><button class="text-action" type="button" @click="router.push({ path: '/tasks', query: { case_id: currentCaseId } })">打开任务中心</button></div>
        <AsyncWorkflowRunsPanel :case-id="currentCaseId" :workflow-id="route.query.workflow_id" title="案件执行进度" description="异步处理、审查和文档任务的实时状态。" />
        <div class="compact-record-list">
          <button v-for="item in caseActivities" :key="`${item.type}-${item.id}`" type="button" @click="openRecord(item)">
            <span>{{ recordTypeLabel(item.type) }}</span><strong>{{ item.title }}</strong><time>{{ formatDate(item.created_at) }}</time><em>{{ caseStatusLabel(item.status) }}</em>
          </button>
          <div v-if="!caseActivities.length" class="legal-empty">案件暂无工作记录。</div>
        </div>
      </section>

      <section v-else-if="activeCaseTab === 'deadlines'" class="matter-panel">
        <div class="legal-section-heading"><h2>关键日期</h2></div>
        <AsyncLegalDeadlines :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'sources'" class="matter-panel">
        <div class="legal-section-heading"><h2>法律资料</h2></div><AsyncLegalSourcesTab :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'billing'" class="matter-panel">
        <div class="legal-section-heading"><h2>计时计费</h2></div><AsyncLegalBilling :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'contracts'" class="matter-panel">
        <div class="legal-section-heading"><h2>合同台账</h2></div><AsyncLegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'portal'" class="matter-panel">
        <div class="legal-section-heading"><h2>客户门户</h2></div><AsyncLegalPortalTab :organization-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else class="matter-panel"><div class="legal-empty">未找到案件工作页面。</div></section>
    </template>

    <WorkbenchHome
      v-else
      :overview="overviewQuery.data.value"
      :cases="cases"
      :org-missing="overviewQuery.isSuccess.value && !hasOrg"
      @create-case="caseDialog?.open()"
      @open-cases="goToView('cases')"
      @open-review="goToView('review')"
      @open-case="openMatter"
      @open-documents="router.push('/documents')"
      @open-research="goToView('research')"
      @open-tasks="router.push('/tasks')"
      @open-chat="router.push('/chat')"
    />

    <CaseCreateDialog ref="caseDialog" :org-id="currentOrgId" @created="handleCaseCreated" />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import 'element-plus/es/components/select/style/css'
import { useRoute, useRouter } from 'vue-router'
import { legalWorkspace } from '../api'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'
import CaseCreateDialog from '../components/legal/CaseCreateDialog.vue'
import CaseHeader from '../components/legal/CaseHeader.vue'
import LegalContractTab from '../components/legal/LegalContractTab.vue'
import LegalDraftsTab from '../components/legal/LegalDraftsTab.vue'
import LegalReviewTab from '../components/legal/LegalReviewTab.vue'
import WorkbenchHome from '../components/legal/WorkbenchHome.vue'
import { categoryLabel, statusLabel } from '../composables/useLegalWorkspacePresentation'

const AsyncLegalBilling = defineAsyncComponent(() => import('./LegalBilling.vue'))
const AsyncLegalDeadlines = defineAsyncComponent(() => import('./LegalDeadlines.vue'))
const AsyncLegalContracts = defineAsyncComponent(() => import('./LegalContracts.vue'))
// 无跨 tab ref 调用（prefill/refresh 等）的面板按需加载，控制 LegalWorkspace chunk 体积；
// LegalContractTab / LegalDraftsTab / LegalReviewTab 因存在跳转后立即 prefill/refresh 的时序依赖保持静态。
const AsyncCaseDocumentsPanel = defineAsyncComponent(() => import('../components/legal/CaseDocumentsPanel.vue'))
const AsyncLegalConsultationsTab = defineAsyncComponent(() => import('../components/legal/LegalConsultationsTab.vue'))
const AsyncLegalSourcesTab = defineAsyncComponent(() => import('../components/legal/LegalSourcesTab.vue'))
const AsyncLegalPortalTab = defineAsyncComponent(() => import('../components/legal/LegalPortalTab.vue'))
const AsyncMatterActivity = defineAsyncComponent(() => import('../components/legal/MatterActivity.vue'))
const AsyncWorkflowRunsPanel = defineAsyncComponent(() => import('../components/legal/WorkflowRunsPanel.vue'))

const route = useRoute()
const router = useRouter()
const caseDialog = ref(null)
const currentCaseId = ref(route.query.case_id ? Number(route.query.case_id) : null)
const caseSearch = ref('')
const reviewTabRef = ref(null)
const draftsTabRef = ref(null)
const contractsTabRef = ref(null)

const overviewQuery = useQuery({ key: qk.legal.overview(), fetcher: () => legalWorkspace.getLegalOverview(), staleTime: 60_000 })
// 自注册且未归属组织的用户 organization_id 为 null：不再回退默认组织（回退会让全部
// 写操作打到无权限的组织上，只能得到"不是该组织成员"的不可理解错误，见 ux-audit P0-1）。
const hasOrg = computed(() => overviewQuery.data.value?.organization_id != null)
const currentOrgId = computed(() => overviewQuery.data.value?.organization_id || null)
const casesQuery = useQuery({ key: () => qk.legal.cases(currentOrgId.value), fetcher: () => legalWorkspace.listCases(currentOrgId.value), staleTime: 30_000, enabled: () => overviewQuery.isSuccess.value && hasOrg.value })
const cases = computed(() => casesQuery.data.value || [])
const view = computed(() => route.query.view || '')
const currentCase = computed(() => cases.value.find((matter) => matter.id === currentCaseId.value) || null)
const activeCaseTab = computed(() => route.query.tab || 'overview')
const matterTabs = [
  { key: 'overview', label: '案件概览' },
  { key: 'documents', label: '文档' },
  { key: 'consultation', label: '法律咨询' },
  { key: 'contract', label: '合同审查' },
  { key: 'draft', label: '文书' },
  { key: 'review', label: '审核' },
  { key: 'tasks', label: '任务' },
  { key: 'deadlines', label: '关键日期' },
]
const legacyToolValue = computed(() => ['billing', 'contracts', 'portal'].includes(activeCaseTab.value) ? activeCaseTab.value : '')

watch(() => route.query.case_id, (id) => {
  if (id && Number(id) !== currentCaseId.value) currentCaseId.value = Number(id)
})

watch([cases, () => route.query.case_id, () => route.query.tab, () => route.query.view], ([items, caseId, tab, viewName]) => {
  const hasMatterContext = Boolean(caseId || tab) && !viewName
  if (!hasMatterContext) {
    currentCaseId.value = null
    return
  }
  if (!items.length) {
    currentCaseId.value = null
    return
  }
  if (!items.some((matter) => matter.id === currentCaseId.value)) {
    const routeCase = items.find((matter) => matter.id === Number(caseId))
    const active = routeCase || items.find((matter) => matter.status === 'in_progress') || items[0]
    currentCaseId.value = active.id
  }
}, { immediate: true })

const caseItemsQuery = useQuery({
  key: () => qk.legal.caseItems(currentOrgId.value, currentCaseId.value),
  fetcher: () => legalWorkspace.listCaseItems(currentOrgId.value, currentCaseId.value),
  staleTime: 15_000,
  enabled: () => Boolean(currentCaseId.value && currentOrgId.value),
})

const matterActivityQuery = useQuery({
  key: () => qk.legal.matterActivity(currentCaseId.value),
  fetcher: () => legalWorkspace.listMatterActivity(currentCaseId.value),
  staleTime: 10_000,
  enabled: () => Boolean(currentCaseId.value),
})

const caseItems = computed(() => caseItemsQuery.data.value || {})
const caseRecords = computed(() => [
  ...(caseItems.value.consultations || []).map((item) => ({ ...item, type: 'consultation', title: item.question || '法律咨询' })),
  ...(caseItems.value.contract_reviews || []).map((item) => ({ ...item, type: 'contract_review', title: item.title || '合同审查' })),
  ...(caseItems.value.drafts || []).map((item) => ({ ...item, type: 'draft', title: item.title || item.document_type || '法律文书' })),
].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || ''))))
const legacyActivities = computed(() => caseRecords.value.map((item) => ({ ...item, description: `${recordTypeLabel(item.type)} · ${caseStatusLabel(item.status)}` })))
const caseActivities = computed(() => {
  const items = matterActivityQuery.data.value?.items || []
  if (!items.length) return legacyActivities.value
  return items.map((item) => ({
    ...item,
    type: item.target_type || item.event_type,
    id: item.id,
    description: item.summary || item.event_type,
  }))
})
const keyMaterials = computed(() => caseRecords.value.filter((item) => item.type !== 'consultation'))
const pendingItems = computed(() => caseRecords.value.filter((item) => ['pending_review', 'needs_lawyer_review', 'needs_facts', 'returned_for_facts'].includes(item.status)))
const pendingReviewCount = computed(() => caseRecords.value.filter((item) => ['pending_review', 'needs_lawyer_review'].includes(item.status)).length)
const caseUpdatedLabel = computed(() => currentCase.value?.updated_at ? `更新于 ${formatDate(currentCase.value.updated_at)}` : '案件摘要')
const filteredCases = computed(() => {
  const keyword = caseSearch.value.trim().toLocaleLowerCase()
  if (!keyword) return cases.value
  return cases.value.filter((matter) => [matter.title, matter.client_name, matter.opposing_party, matter.description].some((value) => String(value || '').toLocaleLowerCase().includes(keyword)))
})

const recordTypeLabel = (type) => ({ consultation: '咨询', contract_review: '审查', draft: '文书' }[type] || '记录')
const caseStatusLabel = (status) => ({ in_progress: '进行中', closed: '已结案', archived: '已归档' }[status] || statusLabel(status) || '处理中')
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

const setMatterTab = (tab) => {
  router.replace({ query: { ...route.query, view: undefined, tab: tab || undefined } })
}
const goToView = (nextView) => router.push({ path: '/legal-workspace', query: { view: nextView } })
const handleCaseSelect = (caseId) => {
  router.replace({ query: { ...route.query, view: undefined, case_id: caseId, tab: activeCaseTab.value === 'overview' ? undefined : activeCaseTab.value } })
}
const openMatter = (matter) => router.push({ path: '/legal-workspace', query: { case_id: matter.id } })
const handleCaseCreated = async (caseId) => {
  currentCaseId.value = caseId
  await casesQuery.refetch()
  await router.replace({ path: '/legal-workspace', query: { case_id: caseId } })
}
const openRecord = (item) => setMatterTab(({ consultation: 'consultation', contract_review: 'contract', draft: 'draft' })[item.type] || 'overview')
const continueMatter = () => {
  const item = pendingItems.value[0]
  if (item) return openRecord(item)
  setMatterTab('contract')
}
const refreshReviewQueue = () => reviewTabRef.value?.refresh()

const CATEGORY_TO_DRAFT_TYPE = {
  labor_dispute: 'labor_arbitration_application',
  private_lending: 'private_lending_complaint',
  consumer_dispute: 'consumer_complaint',
}
const handleGoToDraftFromConsult = (consultResult) => {
  if (!consultResult) return
  const type = CATEGORY_TO_DRAFT_TYPE[consultResult.category]
  const facts = (consultResult.known_facts || []).join('；')
  draftsTabRef.value?.prefill(type, facts ? { 事实与理由: facts } : {})
  setMatterTab('draft')
  ElMessage.info(type ? '已带入咨询案情，请补充文书字段' : '已带入咨询案情，请选择文书类型')
}
const handleGoToReviewFromConsult = (consultResult) => {
  if (!consultResult) return
  contractsTabRef.value?.prefill(`${categoryLabel(consultResult.category)}关联审查`, (consultResult.known_facts || []).join('\n'))
  setMatterTab('contract')
  ElMessage.info('已带入咨询案情，请补充合同原文后开始审查')
}
const downloadText = (filename, content) => {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.workspace-page { display: grid; gap: 0; }
.matter-toolbar { display: flex; align-items: center; gap: 10px; min-height: 38px; margin-bottom: 18px; }
.matter-select { width: min(360px, 55vw); }
.quiet-action, .text-action { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; cursor: pointer; }
.quiet-action:hover, .text-action:hover { color: var(--color-primary-hover); text-decoration: underline; }
.new-matter { margin-left: auto; }
.legal-matter-tabs { margin-top: 18px; }
.matter-tools-select { flex: 0 0 auto; min-height: 30px; margin: auto 0 7px auto; border: 0; background: transparent; color: var(--color-text-muted); font: inherit; font-size: 12px; cursor: pointer; }
.matter-overview { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(260px, 0.8fr); gap: 36px; padding-top: 24px; }
.matter-main-column, .matter-side-column { display: grid; align-content: start; gap: 26px; min-width: 0; }
.matter-main-column > .legal-section + .legal-section, .matter-side-column > .legal-section + .legal-section { padding-top: 18px; border-top: 1px solid var(--color-border); }
.matter-summary { max-width: 820px; margin: 0; color: var(--color-text-secondary); font-size: 14px; line-height: 1.8; white-space: pre-line; }
.inline-empty { padding: 8px 0; text-align: left; }
.material-list { display: grid; }
.material-row { display: grid; grid-template-columns: 82px minmax(0,1fr) auto; align-items: center; gap: 12px; min-height: 42px; padding: 8px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; text-align: left; cursor: pointer; }
.material-row:hover, .pending-row:hover, .workflow-list button:hover { background: #F8FAFB; }
.material-kind { color: var(--color-text-muted); font-size: 11px; }
.material-row strong { overflow: hidden; color: var(--color-text); font-size: 13px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.pending-list { display: grid; }
.pending-row { display: grid; grid-template-columns: 8px minmax(0,1fr); gap: 10px; width: 100%; padding: 11px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; text-align: left; cursor: pointer; }
.pending-row > span:last-child { display: grid; gap: 4px; }
.pending-row strong { overflow: hidden; font-size: 12px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.pending-row small { color: var(--color-text-muted); font-size: 11px; }
.pending-mark { width: 6px; height: 6px; margin-top: 4px; border-radius: 50%; background: var(--color-primary); }
.mark-high { background: var(--color-danger); }
.mark-medium { background: var(--color-warning); }
.pending-empty { margin: 0; line-height: 1.7; }
.workflow-list { display: grid; }
.workflow-list button { display: flex; align-items: center; justify-content: space-between; min-height: 38px; padding: 5px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text-secondary); text-align: left; cursor: pointer; }
.workflow-list span { font-size: 12px; }
.workflow-list strong { color: var(--color-text); font-size: 12px; font-weight: 550; }
.dates-section .legal-muted { margin: 0; line-height: 1.65; }
.matter-assistant-strip { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: 14px; padding-top: 18px; border-top: 1px solid var(--color-border); }
.matter-assistant-strip > div { display: grid; gap: 4px; }
.assistant-kicker { margin: 0; color: var(--color-primary); font-size: 11px; font-weight: 600; }
.matter-assistant-strip strong { color: var(--color-text); font-size: 12px; font-weight: 600; }
.matter-assistant-strip span { color: var(--color-text-muted); font-size: 11px; line-height: 1.55; }
.matter-panel { min-width: 0; padding-top: 22px; }
.matter-panel > .legal-section-heading { margin-bottom: 16px; }
.matter-panel .legal-section-heading h2 { margin: 0; font-size: 16px; }
.matter-panel .legal-section-heading span { display: block; margin-top: 4px; }
.related-records { margin-top: 26px; padding-top: 18px; border-top: 1px solid var(--color-border); }
.compact-record-list { display: grid; }
.compact-record-list button { display: grid; grid-template-columns: 74px minmax(0, 1fr) minmax(110px, auto) auto; align-items: center; gap: 14px; min-height: 44px; padding: 8px 4px; border: 0; border-bottom: 1px solid var(--color-border-light); background: transparent; color: var(--color-text-secondary); text-align: left; cursor: pointer; }
.compact-record-list button:hover { background: #F8FAFB; }
.compact-record-list span, .compact-record-list time, .compact-record-list em { color: var(--color-text-muted); font-size: 11px; font-style: normal; }
.compact-record-list strong { color: var(--color-text); font-size: 12px; font-weight: 550; }
.case-list-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 0 10px; }
.case-list-toolbar input { width: min(420px, 100%); height: 36px; padding: 0 10px; border: 1px solid var(--color-border); border-radius: 4px; background: white; color: var(--color-text); font: inherit; font-size: 13px; }
.case-list-toolbar input:focus { border-color: var(--color-primary); outline: 2px solid var(--color-primary-subtle); }
.case-list { border-top: 1px solid var(--color-border); }
.case-list-row { display: grid; width: 100%; grid-template-columns: minmax(220px, 1.4fr) minmax(110px, .7fr) minmax(220px, 1fr) auto; align-items: center; gap: 16px; min-height: 66px; padding: 10px 12px; border: 0; border-bottom: 1px solid var(--color-border); background: transparent; color: var(--color-text); text-align: left; cursor: pointer; }
.case-list-row:hover { background: white; }
.case-list-main { display: grid; gap: 4px; min-width: 0; }
.case-list-main strong { overflow: hidden; font-size: 14px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.case-list-main span, .case-list-type, .case-list-counts { color: var(--color-text-muted); font-size: 12px; }
.case-list-status { padding-left: 9px; border-left: 2px solid var(--color-success); color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.status-closed { border-color: var(--color-info); }
.status-archived { border-color: var(--color-warning); }
.workspace-primary { min-height: 36px; padding: 0 14px; border: 1px solid var(--color-primary); border-radius: 4px; background: var(--color-primary); color: white; font: inherit; font-size: 13px; cursor: pointer; }
.workspace-primary:hover { background: var(--color-primary-hover); }
.no-matters { display: grid; justify-items: start; align-content: center; min-height: 52vh; padding: 24px; border-top: 1px solid var(--color-border); }
.no-matters h1 { margin: 0; font-size: 22px; }
.no-matters p { max-width: 540px; margin: 8px 0 20px; color: var(--color-text-secondary); line-height: 1.7; }
@media (max-width: 900px) {
  .matter-overview { grid-template-columns: minmax(0, 1fr); gap: 26px; }
  .matter-side-column { grid-template-columns: repeat(2, minmax(0,1fr)); gap: 18px 24px; }
  .matter-side-column > .legal-section + .legal-section { padding-top: 0; border-top: 0; }
  .case-list-row { grid-template-columns: minmax(0, 1fr) auto; }
  .case-list-type, .case-list-counts { grid-column: 1; }
  .case-list-status { grid-column: 2; grid-row: 1; }
}
@media (max-width: 600px) {
  .matter-toolbar { gap: 6px; flex-wrap: wrap; }
  .matter-select { width: 100%; }
  .new-matter { margin-left: auto; }
  .matter-overview { padding-top: 18px; }
  .matter-side-column { grid-template-columns: minmax(0,1fr); }
  .matter-side-column > .legal-section + .legal-section { padding-top: 16px; border-top: 1px solid var(--color-border); }
  .material-row { grid-template-columns: 66px minmax(0, 1fr); }
  .material-row > .legal-muted { grid-column: 2; }
  .compact-record-list button { grid-template-columns: 65px minmax(0, 1fr); gap: 5px 10px; }
  .compact-record-list time, .compact-record-list em { grid-column: 2; }
  .case-list-row { gap: 5px 12px; padding: 11px 6px; }
  .case-list-counts { font-size: 10px; }
  .matter-assistant-strip { align-items: flex-start; grid-template-columns: minmax(0,1fr); }
}
</style>
