<template>
  <div class="legal-page workspace-page">
    <template v-if="view === 'cases'">
      <header class="app-page-head">
        <div class="app-page-head-main">
          <h1 class="app-page-title">案件</h1>
          <p class="app-page-sub">按案件集中查看工作进展、咨询、审查与文书。</p>
        </div>
        <div class="app-page-head-actions">
          <button class="workspace-primary" type="button" :disabled="!hasOrg" :title="!hasOrg ? '你还未加入组织，暂无法创建案件' : undefined" @click="caseDialog?.open()">新建案件</button>
        </div>
      </header>

      <div class="case-list-toolbar">
        <input v-model="caseSearch" aria-label="搜索案件" placeholder="搜索案件名称、客户或案情" />
        <span class="legal-muted">{{ filteredCases.length }} 个案件</span>
      </div>
      <div class="case-list">
        <button v-for="matter in filteredCases" :key="matter.id" class="app-thread case-card" type="button" @click="openMatter(matter)">
          <span class="case-card-top">
            <strong class="app-thread-title">{{ matter.title }}</strong>
            <span class="app-tag" :class="caseStatusTagClass(matter.status)">{{ caseStatusLabel(matter.status) }}</span>
          </span>
          <span class="app-thread-meta">
            {{ matter.client_name || '未登记客户' }}<template v-if="matter.opposing_party"> · 对方 {{ matter.opposing_party }}</template>
          </span>
          <span class="app-thread-meta">
            {{ categoryLabel(matter.case_type) }} · 咨询 {{ matter.item_counts?.consultations || 0 }} · 审查 {{ matter.item_counts?.reviews || 0 }} · 文书 {{ matter.item_counts?.drafts || 0 }}
          </span>
        </button>
        <div v-if="!filteredCases.length" class="legal-empty case-list-empty">
          {{ cases.length ? '没有符合条件的案件' : '还没有案件，创建一个案件以开始归档工作。' }}
          <button v-if="!cases.length" type="button" class="empty-cta" :disabled="!hasOrg" @click="caseDialog?.open()">新建案件</button>
        </div>
      </div>
    </template>

    <template v-else-if="view === 'research'">
      <header class="app-page-head">
        <div class="app-page-head-main">
          <h1 class="app-page-title">法律资料</h1>
          <p class="app-page-sub">查看法规、案例与业务模板。</p>
        </div>
      </header>
      <AsyncLegalSourcesTab />
    </template>

    <template v-else-if="view === 'review'">
      <!-- 页头由 LegalReviewTab 自带（含 SLA 指标），这里不再重复一层标题 -->
      <AsyncLegalReviewTab ref="reviewTabRef" />
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

      <section v-if="activeCaseTab === 'overview'" class="matter-overview app-workspace-body">
        <div class="matter-main-column">
          <section class="app-card">
            <div class="app-card-head">
              <h2>案情摘要</h2>
              <span class="app-card-head-meta">{{ caseUpdatedLabel }}</span>
            </div>
            <div class="app-card-body">
              <p v-if="currentCase.description" class="matter-summary">{{ currentCase.description }}</p>
              <p v-else class="legal-muted">尚未填写案情摘要。</p>
            </div>
          </section>

          <section class="app-card">
            <div class="app-card-head">
              <h2>最近活动</h2>
              <button class="text-action" type="button" @click="setMatterTab('tasks')">查看全部</button>
            </div>
            <div class="app-card-body">
              <AsyncMatterActivity :items="caseActivities.slice(0, 6)" />
            </div>
          </section>

          <section class="app-card">
            <div class="app-card-head">
              <h2>关键文档与成果</h2>
              <button class="text-action" type="button" @click="setMatterTab('documents')">打开文档</button>
            </div>
            <div class="app-card-body">
              <div v-if="keyMaterials.length" class="material-list">
                <button v-for="item in keyMaterials.slice(0, 5)" :key="`${item.type}-${item.id}`" class="material-row" type="button" @click="openRecord(item)">
                  <span class="material-kind">{{ recordTypeLabel(item.type) }}</span>
                  <strong>{{ item.title }}</strong>
                  <span class="legal-muted">{{ caseStatusLabel(item.status) }}</span>
                </button>
              </div>
              <div v-else class="legal-empty inline-empty">还没有合同审查或文书成果。</div>
            </div>
          </section>
        </div>

        <aside class="matter-side-column app-rail">
          <section class="app-card">
            <div class="app-card-head">
              <h2>待处理事项</h2>
              <span class="app-card-head-meta">{{ pendingItems.length }}</span>
            </div>
            <div class="app-card-body">
              <div v-if="pendingItems.length" class="pending-list">
                <button v-for="item in pendingItems.slice(0, 5)" :key="`${item.type}-${item.id}`" class="pending-row" type="button" @click="openRecord(item)">
                  <span class="pending-mark" :class="`mark-${item.risk_level || 'normal'}`"></span>
                  <span><strong>{{ item.title }}</strong><small>{{ recordTypeLabel(item.type) }} · {{ caseStatusLabel(item.status) }}</small></span>
                </button>
              </div>
              <p v-else class="legal-muted pending-empty">目前没有待处理的咨询、审查或文书。</p>
            </div>
          </section>

          <section class="app-card">
            <div class="app-card-head">
              <h2>案件工作流</h2>
            </div>
            <div class="app-card-body">
              <div class="workflow-list">
                <button type="button" @click="setMatterTab('consultation')"><span>法律咨询</span><strong>{{ currentCase.item_counts?.consultations || 0 }}</strong></button>
                <button type="button" @click="setMatterTab('contract')"><span>合同审查</span><strong>{{ currentCase.item_counts?.reviews || 0 }}</strong></button>
                <button type="button" @click="setMatterTab('draft')"><span>文书</span><strong>{{ currentCase.item_counts?.drafts || 0 }}</strong></button>
                <button type="button" @click="setMatterTab('review')"><span>律师审核</span><strong>{{ pendingReviewCount }}</strong></button>
              </div>
            </div>
          </section>

          <AsyncWorkflowRunsPanel
            :case-id="currentCaseId"
            title="执行进度"
            description="查看案件动作的处理进度。"
            :limit="5"
          />

          <section class="app-card">
            <div class="app-card-head">
              <h2>关键日期</h2>
              <button class="text-action" type="button" @click="setMatterTab('deadlines')">管理日期</button>
            </div>
            <div class="app-card-body">
              <p class="legal-muted dates-hint">在案件日程中记录开庭、答辩与履行期限。</p>
            </div>
          </section>

          <section class="app-card matter-assistant-strip">
            <div>
              <p class="assistant-kicker">案件助手</p>
              <strong>基于当前案件资料继续工作</strong>
              <span>分析案情、查找法源、准备文书，回答会保留在当前案件上下文中。</span>
            </div>
            <button type="button" class="text-action" @click="openCopilot()">打开助手</button>
          </section>
        </aside>
      </section>

      <section v-else-if="activeCaseTab === 'documents'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main">
            <h2 class="app-page-title">文档</h2>
            <p class="app-page-sub">案件材料、合同台账与相关工作记录。</p>
          </div>
        </header>
        <AsyncCaseDocumentsPanel :case-id="currentCaseId" />
        <AsyncLegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
        <section class="app-card related-records">
          <div class="app-card-head"><h2>相关工作记录</h2></div>
          <div class="app-card-body">
            <div v-if="caseActivities.length" class="compact-record-list">
              <button v-for="item in caseActivities" :key="`${item.type}-${item.id}`" type="button" @click="openRecord(item)">
                <span>{{ recordTypeLabel(item.type) }}</span><strong>{{ item.title }}</strong><time>{{ formatDate(item.created_at) }}</time>
              </button>
            </div>
            <div v-else class="legal-empty">案件暂无工作记录。</div>
          </div>
        </section>
      </section>

      <section v-else-if="activeCaseTab === 'consultation'" class="matter-panel">
        <!-- 咨询 tab 自带卡片标题（法律咨询 / 咨询结果 / 历史咨询），这里不再重复一层大标题 -->
        <AsyncLegalConsultationsTab :key="`consultation-${currentCaseId}`" :case-id="currentCaseId" :initial-consultation-id="consultationFocusId" :on-review-submitted="refreshReviewQueue" @go-to-draft="handleGoToDraftFromConsult" @go-to-review="handleGoToReviewFromConsult" />
      </section>

      <section v-else-if="activeCaseTab === 'contract'" class="matter-panel">
        <AsyncLegalContractTab :key="`contract-${currentCaseId}`" ref="contractsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </section>

      <section v-else-if="activeCaseTab === 'draft'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">案件文书</h2></div>
        </header>
        <AsyncLegalDraftsTab :key="`draft-${currentCaseId}`" ref="draftsTabRef" :case-id="currentCaseId" :download-text="downloadText" />
      </section>

      <section v-else-if="activeCaseTab === 'review'" class="matter-panel">
        <!-- 标题由 LegalReviewTab 自带，避免与外层 h2 重复 -->
        <AsyncLegalReviewTab :key="`review-${currentCaseId}`" ref="reviewTabRef" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'tasks'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main">
            <h2 class="app-page-title">案件工作记录</h2>
            <p class="app-page-sub">按时间查看咨询、合同审查与文书处理。</p>
          </div>
          <div class="app-page-head-actions">
            <button class="text-action" type="button" @click="router.push({ path: '/tasks', query: { case_id: currentCaseId } })">打开任务中心</button>
          </div>
        </header>
        <AsyncWorkflowRunsPanel :case-id="currentCaseId" :workflow-id="route.query.workflow_id" title="案件执行进度" description="异步处理、审查和文档任务的实时状态。" />
        <div class="compact-record-list">
          <button v-for="item in caseActivities" :key="`${item.type}-${item.id}`" type="button" @click="openRecord(item)">
            <span>{{ recordTypeLabel(item.type) }}</span><strong>{{ item.title }}</strong><time>{{ formatDate(item.created_at) }}</time><em>{{ caseStatusLabel(item.status) }}</em>
          </button>
          <div v-if="!caseActivities.length" class="legal-empty">案件暂无工作记录。</div>
        </div>
      </section>

      <section v-else-if="activeCaseTab === 'deadlines'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">关键日期</h2></div>
        </header>
        <AsyncLegalDeadlines :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'sources'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">法律资料</h2></div>
        </header><AsyncLegalSourcesTab :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'billing'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">计时计费</h2></div>
        </header>
        <AsyncLegalBilling :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'contracts'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">合同台账</h2></div>
        </header>
        <AsyncLegalContracts :org-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else-if="activeCaseTab === 'portal'" class="matter-panel">
        <header class="app-page-head">
          <div class="app-page-head-main"><h2 class="app-page-title">客户门户</h2></div>
        </header>
        <AsyncLegalPortalTab :organization-id="currentOrgId" :case-id="currentCaseId" />
      </section>

      <section v-else class="matter-panel"><div class="legal-empty">未找到案件工作页面。</div></section>
    </template>

    <WorkbenchHome
      v-else
      :overview="overviewQuery.data.value"
      :cases="cases"
      :org-missing="overviewQuery.isSuccess.value && !hasOrg"
      :cases-loading="casesLoading"
      :cases-error="casesError"
      @create-case="caseDialog?.open()"
      @open-cases="goToView('cases')"
      @open-review="goToView('review')"
      @open-case="openMatter"
      @retry-cases="casesQuery.refetch()"
      @open-documents="router.push('/documents')"
      @open-research="goToView('research')"
      @open-tasks="router.push('/tasks')"
      @open-chat="openCopilot()"
    />

    <CaseCreateDialog ref="caseDialog" :org-id="currentOrgId" @created="handleCaseCreated" />
    <SourceDetailDialog />
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, nextTick, onUnmounted, provide, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import 'element-plus/es/components/select/style/css'
import { useRoute, useRouter } from 'vue-router'
import { legalWorkspace } from '../api'
import { useQuery } from '../query/useQuery'
import { qk } from '../query/keys'
import CaseCreateDialog from '../components/legal/CaseCreateDialog.vue'
import SourceDetailDialog from '../components/legal/SourceDetailDialog.vue'
import CaseHeader from '../components/legal/CaseHeader.vue'
import WorkbenchHome from '../components/legal/WorkbenchHome.vue'
import { categoryLabel, statusLabel } from '../composables/useLegalWorkspacePresentation'
import { clearWorkspaceCrumbs, setWorkspaceCrumbs } from '../composables/workspaceBreadcrumb'
import { useCopilotDock } from '../composables/useCopilotDock'
import { CONSULT_RESULT_KEY } from '../composables/consultationResultKey'

const AsyncLegalBilling = defineAsyncComponent(() => import('./LegalBilling.vue'))
const AsyncLegalDeadlines = defineAsyncComponent(() => import('./LegalDeadlines.vue'))
const AsyncLegalContracts = defineAsyncComponent(() => import('./LegalContracts.vue'))
// 案件主 tab 全部按需加载：合同审查 / 文书 / 审核是页面里最大的三块，
// 拆成独立 chunk 后首屏与概览不再下载；从咨询结果跳转的 prefill 先记 pending，
// 等异步组件挂载、ref 出现后再应用，避免“提示已带入、字段却是空”。
const AsyncLegalContractTab = defineAsyncComponent(() => import('../components/legal/LegalContractTab.vue'))
const AsyncLegalDraftsTab = defineAsyncComponent(() => import('../components/legal/LegalDraftsTab.vue'))
const AsyncLegalReviewTab = defineAsyncComponent(() => import('../components/legal/LegalReviewTab.vue'))
const AsyncCaseDocumentsPanel = defineAsyncComponent(() => import('../components/legal/CaseDocumentsPanel.vue'))
const AsyncLegalConsultationsTab = defineAsyncComponent(() => import('../components/legal/LegalConsultationsTab.vue'))
const AsyncLegalSourcesTab = defineAsyncComponent(() => import('../components/legal/LegalSourcesTab.vue'))
const AsyncLegalPortalTab = defineAsyncComponent(() => import('../components/legal/LegalPortalTab.vue'))
const AsyncMatterActivity = defineAsyncComponent(() => import('../components/legal/MatterActivity.vue'))
const AsyncWorkflowRunsPanel = defineAsyncComponent(() => import('../components/legal/WorkflowRunsPanel.vue'))

const route = useRoute()
const router = useRouter()
// 助手入口收敛：这里不再跳 /chat，而是打开右侧面板（案件上下文由 URL 提供，面板自行解析）
const { openCopilot } = useCopilotDock()
const caseDialog = ref(null)
const currentCaseId = ref(route.query.case_id ? Number(route.query.case_id) : null)
// 咨询结果提升到案件工作区：咨询 tab 切走会被卸载，但用户从咨询生成文书/审查后
// 再切回来仍应看到同一张结果卡；换案件时清空，避免跨案残留。
const consultResult = ref(null)
provide(CONSULT_RESULT_KEY, consultResult)
watch(currentCaseId, () => { consultResult.value = null })
const caseSearch = ref('')
const reviewTabRef = ref(null)
const draftsTabRef = ref(null)
const contractsTabRef = ref(null)
// 从咨询结果跳 tab 时，目标组件是异步的，挂载前 ref 还不存在。
// 先记下要带入的内容，等 ref 解析/挂载后再应用，避免“提示已带入、字段却是空”。
const pendingDraftPrefill = ref(null)
const pendingContractPrefill = ref(null)

const overviewQuery = useQuery({ key: qk.legal.overview(), fetcher: () => legalWorkspace.getLegalOverview(), staleTime: 60_000 })
// 自注册且未归属组织的用户 organization_id 为 null：不再回退默认组织（回退会让全部
// 写操作打到无权限的组织上，只能得到"不是该组织成员"的不可理解错误，见 ux-audit P0-1）。
const hasOrg = computed(() => overviewQuery.data.value?.organization_id != null)
const currentOrgId = computed(() => overviewQuery.data.value?.organization_id || null)
const casesQuery = useQuery({ key: () => qk.legal.cases(currentOrgId.value), fetcher: () => legalWorkspace.listCases(currentOrgId.value), staleTime: 30_000, enabled: () => overviewQuery.isSuccess.value && hasOrg.value })
const cases = computed(() => casesQuery.data.value || [])
// 案件查询三态透传给 WorkbenchHome（ux-audit P1-3/M-13：失败不与"暂无案件"混同）
const casesLoading = computed(() => casesQuery.isLoading.value)
const casesError = computed(() => (casesQuery.isError.value ? casesQuery.error.value : null))
const view = computed(() => route.query.view || '')
const currentCase = computed(() => cases.value.find((matter) => matter.id === currentCaseId.value) || null)
const activeCaseTab = computed(() => route.query.tab || 'overview')
// 咨询 tab 直达焦点（通知/案件记录带 consultation_id 进入时自动恢复结果卡）
const consultationFocusId = computed(() => (activeCaseTab.value === 'consultation' && route.query.consultation_id ? Number(route.query.consultation_id) : null))
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
const caseStatusTagClass = (status) => ({ in_progress: 'is-ok', archived: 'is-pending' }[status] || '')
const formatDate = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'

// 顶栏面包屑由外壳渲染，这里登记当前上下文：案件标题来自接口，只有本页知道；
// 没有案件上下文时留空，外壳回退到栏目名（工作台 / 案件 / 法律资料 / 审核）。
const crumbItems = computed(() => {
  if (view.value === 'cases') return [{ label: '案件' }]
  if (view.value === 'research') return [{ label: '法律资料' }]
  if (view.value === 'review') return [{ label: '审核' }]
  if (currentCase.value) return [{ label: '案件', path: '/cases' }, { label: currentCase.value.title }]
  return []
})
watch(crumbItems, (items) => setWorkspaceCrumbs(items), { immediate: true })
onUnmounted(() => clearWorkspaceCrumbs())

const setMatterTab = (tab) => {
  router.replace({ query: { ...route.query, view: undefined, tab: tab || undefined } })
}
const goToView = (nextView) => router.push({ path: '/legal-workspace', query: { view: nextView } })
const handleCaseSelect = (caseId) => {
  // 切换案件时清掉上一案件的焦点：咨询结果卡、合同审查、文书草稿都不能跨案件残留
  router.replace({ query: {
    ...route.query, view: undefined, case_id: caseId,
    tab: activeCaseTab.value === 'overview' ? undefined : activeCaseTab.value,
    consultation_id: undefined, contract_id: undefined, draft_id: undefined,
  } })
}
const openMatter = (matter) => router.push({ path: '/legal-workspace', query: { case_id: matter.id } })
const handleCaseCreated = async (caseId) => {
  currentCaseId.value = caseId
  await casesQuery.refetch()
  await router.replace({ path: '/legal-workspace', query: { case_id: caseId } })
}
const openRecord = (item) => {
  const tab = ({ consultation: 'consultation', contract_review: 'contract', draft: 'draft' })[item.type] || 'overview'
  // 咨询记录直达时带上 consultation_id，进入 tab 后自动恢复结果卡（D12 回归发现修复）
  if (item.type === 'consultation' && item.id) {
    router.replace({ query: { ...route.query, view: undefined, tab, consultation_id: String(item.id) } })
    return
  }
  setMatterTab(tab)
}
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
const applyPendingDraftPrefill = () => {
  const target = draftsTabRef.value
  if (!target?.prefill || !pendingDraftPrefill.value) return
  target.prefill(pendingDraftPrefill.value.type, pendingDraftPrefill.value.fields)
  pendingDraftPrefill.value = null
}
const applyPendingContractPrefill = () => {
  const target = contractsTabRef.value
  if (!target?.prefill || !pendingContractPrefill.value) return
  target.prefill(pendingContractPrefill.value.title, pendingContractPrefill.value.content)
  pendingContractPrefill.value = null
}
// 异步组件解析完成后 ref 才会出现：tab 挂载时补一次应用。
watch(draftsTabRef, applyPendingDraftPrefill, { flush: 'post' })
watch(contractsTabRef, applyPendingContractPrefill, { flush: 'post' })
// 用户没等到目标 tab 挂载就切走：丢弃待带入内容，避免下次打开 tab 时突然出现旧案情。
watch(activeCaseTab, (tab) => {
  if (tab !== 'draft') pendingDraftPrefill.value = null
  if (tab !== 'contract') pendingContractPrefill.value = null
})
const handleGoToDraftFromConsult = (consultResult) => {
  if (!consultResult) return
  const type = CATEGORY_TO_DRAFT_TYPE[consultResult.category]
  const facts = (consultResult.known_facts || []).join('；')
  pendingDraftPrefill.value = { type, fields: facts ? { 事实与理由: facts } : {} }
  setMatterTab('draft')
  nextTick(applyPendingDraftPrefill)
  ElMessage.info(type ? '已带入咨询案情，请补充文书字段' : '已带入咨询案情，请选择文书类型')
}
const handleGoToReviewFromConsult = (consultResult) => {
  if (!consultResult) return
  pendingContractPrefill.value = {
    title: `${categoryLabel(consultResult.category)}关联审查`,
    content: (consultResult.known_facts || []).join('\n'),
  }
  setMatterTab('contract')
  nextTick(applyPendingContractPrefill)
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

/* 案件切换条留在标题上方一行；标题区（CaseHeader）只放标题、状态与主操作 */
.matter-toolbar { display: flex; align-items: center; gap: var(--space-3); margin-bottom: var(--space-4); }
.matter-select { width: min(300px, 55vw); }
.quiet-action, .text-action { border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: var(--text-sm); font-weight: 500; cursor: pointer; }
.quiet-action:hover, .text-action:hover { color: var(--color-primary-hover); text-decoration: underline; }
.new-matter { margin-left: auto; }
.legal-matter-tabs { margin-top: var(--space-4); }
.matter-tools-select { flex: 0 0 auto; min-height: 30px; margin: auto 0 7px auto; border: 0; background: transparent; color: var(--color-text-muted); font: inherit; font-size: var(--text-sm); cursor: pointer; }
/* 概览：主栏卡片 + 右栏 rail（列宽由全局 .app-workspace-body 决定，≤1100px 自动堆叠） */
.matter-overview { padding-top: var(--space-6); }
.matter-main-column { display: grid; align-content: start; gap: var(--space-4); min-width: 0; }
.matter-side-column { min-width: 0; }
.app-card-head .text-action { margin-left: auto; }
.matter-summary { margin: 0; color: var(--color-text-secondary); font-size: var(--text-base); line-height: 1.85; white-space: pre-line; }
.inline-empty { padding: var(--space-2) 0; text-align: left; }
.material-list, .pending-list, .workflow-list { display: grid; }
.material-row { display: grid; grid-template-columns: 74px minmax(0,1fr) auto; align-items: center; gap: var(--space-3); min-height: 40px; padding: 8px 6px; border: 0; border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: transparent; text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.material-row:last-child, .pending-row:last-child, .workflow-list button:last-child { border-bottom: 0; }
.material-row:hover, .pending-row:hover, .workflow-list button:hover { background: var(--color-surface-hover); }
.material-kind { color: var(--color-text-muted); font-size: var(--text-xs); }
.material-row strong { overflow: hidden; color: var(--color-text); font-size: var(--text-sm); font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.pending-row { display: grid; grid-template-columns: 8px minmax(0,1fr); gap: 10px; width: 100%; padding: 10px 6px; border: 0; border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: transparent; text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.pending-row > span:last-child { display: grid; gap: 3px; }
.pending-row strong { overflow: hidden; font-size: var(--text-sm); font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.pending-row small { color: var(--color-text-muted); font-size: var(--text-xs); }
.pending-mark { width: 7px; height: 7px; margin-top: 5px; border-radius: var(--radius-full); background: var(--color-primary); }
.mark-high { background: var(--color-danger); }
.mark-medium { background: var(--color-warning); }
.pending-empty { margin: 0; line-height: 1.7; }
.workflow-list button { display: flex; align-items: center; justify-content: space-between; min-height: 38px; padding: 6px; border: 0; border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: transparent; color: var(--color-text-secondary); text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.workflow-list span { font-size: var(--text-sm); }
.workflow-list strong { color: var(--color-text); font-size: var(--text-base); font-weight: 600; }
.dates-hint { margin: 0; line-height: 1.65; }

/* 案件助手入口：既是卡片也是双列说明条 */
.matter-assistant-strip { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: var(--space-4); padding: var(--space-4) var(--space-5); }
.matter-assistant-strip > div { display: grid; gap: 4px; }
.assistant-kicker { margin: 0; color: var(--color-primary); font-size: var(--text-xs); font-weight: 600; }
.matter-assistant-strip strong { color: var(--color-text); font-size: var(--text-sm); font-weight: 600; }
.matter-assistant-strip span { color: var(--color-text-muted); font-size: var(--text-xs); line-height: 1.6; }
.matter-panel { display: grid; gap: var(--space-4); min-width: 0; padding-top: var(--space-5); }
.matter-panel > .app-page-head { margin-bottom: 0; }
.related-records { margin-top: 0; }
.related-records .app-card-body { padding-top: var(--space-2); }
.compact-record-list { display: grid; }
.compact-record-list button { display: grid; grid-template-columns: 74px minmax(0, 1fr) minmax(110px, auto) auto; align-items: center; gap: var(--space-3); min-height: 44px; padding: 8px 6px; border: 0; border-bottom: 1px solid var(--color-border-light); border-radius: var(--radius-sm); background: transparent; color: var(--color-text-secondary); text-align: left; cursor: pointer; transition: background var(--transition-fast); }
.compact-record-list button:hover { background: var(--color-surface-hover); }
.compact-record-list span, .compact-record-list time, .compact-record-list em { color: var(--color-text-muted); font-size: var(--text-xs); font-style: normal; }
.compact-record-list strong { color: var(--color-text); font-size: var(--text-sm); font-weight: 550; }

/* 案件列表：卡片网格（方向 B 的内容卡片化） */
.case-list-toolbar { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); margin: var(--space-6) 0 var(--space-4); }
.case-list-toolbar input { width: min(420px, 100%); height: 38px; padding: 0 var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text); font: inherit; font-size: var(--text-sm); }
.case-list-toolbar input:focus { border-color: var(--color-primary); outline: 2px solid var(--color-primary-subtle); }
.case-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: var(--space-3); }
.case-card { gap: 6px; }
.case-card-top { display: flex; align-items: center; gap: var(--space-2); }
.case-card-top .app-thread-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.case-list-empty { grid-column: 1 / -1; }
/* .workspace-primary 已统一到 design-tokens.css（全局类），这里不再重复定义 */

@media (max-width: 900px) {
  .matter-side-column { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 600px) {
  .matter-toolbar { gap: 6px; flex-wrap: wrap; }
  .matter-select { width: 100%; }
  .material-row { grid-template-columns: 62px minmax(0, 1fr); }
  .material-row > .legal-muted { grid-column: 2; }
  .compact-record-list button { grid-template-columns: 65px minmax(0, 1fr); gap: 5px 10px; }
  .compact-record-list time, .compact-record-list em { grid-column: 2; }
  .matter-assistant-strip { align-items: flex-start; grid-template-columns: minmax(0,1fr); }
}
</style>
