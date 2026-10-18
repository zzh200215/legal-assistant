<template>
  <div class="portal-workspace">
    <!-- 页级标题由父视图提供（LegalWorkspace.vue 的 .app-page-head「客户门户」，与计时计费/合同台账同口径），
         本组件不再自带标题，避免双标题。 -->

    <!-- 无门户管理权限：保留原说明与降级入口，卡片皮肤拉回方向 B -->
    <section v-if="!canManagePortal" class="app-card">
      <div class="app-card-body portal-permission-card">
        <el-empty description="客户门户的链接、品牌与访问分析由组织管理员或审核律师管理。" :image-size="80" />
        <p class="portal-permission-hint">你仍可在下方"案件进度更新"中记录案件进展。</p>
      </div>
    </section>

    <!-- 运营读数：4 张数据卡（与工作台首页同为 .app-stat 口径，数值含义与旧版一致） -->
    <section v-if="canManagePortal" class="app-metric-grid-4" aria-label="门户运营摘要">
      <div class="app-stat">
        <span class="app-stat-label">生效中</span>
        <div class="app-stat-value">{{ portalSummary.active }}</div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">3 天内到期</span>
        <div class="app-stat-value">{{ portalSummary.expiring }}</div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">累计访问</span>
        <div class="app-stat-value">{{ portalSummary.accesses }}</div>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">最近访问</span>
        <div class="app-stat-value portal-stat-text">{{ portalSummary.lastAccess }}</div>
      </div>
    </section>

    <!-- 品牌配置：保存动作留在卡头右侧 -->
    <section v-if="canManagePortal" class="app-card">
      <div class="app-card-head">
        <h2>客户门户品牌</h2>
        <span class="app-card-head-meta">客户打开门户时展示的律所标识与欢迎语</span>
        <el-button size="small" :loading="brandingSaving" @click="saveBranding">保存品牌配置</el-button>
      </div>
      <div class="app-card-body">
        <el-form label-width="120px" label-position="left">
          <el-form-item label="律所 Logo URL">
            <el-input v-model="branding.portal_logo_url" placeholder="https://... 图片直链（可选）" maxlength="512" clearable />
          </el-form-item>
          <el-form-item label="欢迎语">
            <el-input v-model="branding.portal_welcome_message" placeholder="客户打开门户时展示的欢迎语（可选）" maxlength="256" clearable />
          </el-form-item>
        </el-form>
      </div>
    </section>

    <!-- 门户链接：创建入口在卡头，与计费页「创建费用通知单」同一口径 -->
    <section v-if="canManagePortal" class="app-card">
      <div class="app-card-head">
        <h2>客户门户链接</h2>
        <span class="app-card-head-meta">共 {{ portalLinks.length }} 条</span>
        <button class="workspace-primary is-compact" type="button" @click="showPortalDialog = true">创建门户链接</button>
      </div>
      <div class="app-card-body">
        <el-table :data="portalLinks" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="token_prefix" label="令牌前缀" width="120" />
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <span class="app-tag" :class="row.aggregate_case ? 'is-info' : ''">{{ row.aggregate_case ? '聚合' : '定向' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="有效期">
            <template #default="{ row }">
              <span>{{ formatDate(row.expires_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="access_count" label="访问次数" width="100" />
          <el-table-column label="最近访问" width="150">
            <template #default="{ row }">{{ formatDate(row.last_accessed_at) || '尚未访问' }}</template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <span class="app-tag" :class="portalStatus(row).tagClass">{{ portalStatus(row).label }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button v-if="row.status === 'active'" size="small" text type="danger" @click="revokePortalLink(row)">撤销</el-button>
              <span v-else class="app-tag">已失效</span>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>

    <!-- 访问分析：周期切换与图表读数据均保留，只换成卡片版式 -->
    <section v-if="canManagePortal" class="app-card">
      <div class="app-card-head portal-analytics-head">
        <h2>访问分析</h2>
        <span class="app-card-head-meta">仅显示当前案件的聚合访问数据</span>
        <select v-model="analyticsDays" class="portal-period-select" aria-label="访问分析周期" :disabled="analyticsLoading" @change="loadPortalAnalytics">
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
      </div>
      <div class="app-card-body analytics-body">
        <div v-if="analyticsLoading" class="portal-analytics-empty">正在加载访问数据…</div>
        <template v-else>
          <div class="analytics-metrics">
            <div><span class="app-stat-label">成功访问</span><strong>{{ portalAnalytics.summary?.visits || 0 }}</strong></div>
            <div><span class="app-stat-label">去重访客</span><strong>{{ portalAnalytics.summary?.unique_visitors || 0 }}</strong></div>
            <div><span class="app-stat-label">活跃链接</span><strong>{{ portalAnalytics.summary?.active_links || 0 }}</strong></div>
            <div><span class="app-stat-label">被拒访问</span><strong>{{ portalAnalytics.summary?.denied || 0 }}</strong></div>
          </div>
          <div v-if="portalAnalytics.daily?.length" class="analytics-chart" aria-label="每日访问趋势">
            <div v-for="point in portalAnalytics.daily" :key="point.date" class="analytics-day">
              <span class="analytics-bar-wrap"><i :style="{ height: `${barHeight(point.visits)}%` }"></i></span>
              <strong>{{ point.visits }}</strong>
              <time>{{ point.date.slice(5) }}</time>
            </div>
          </div>
          <div v-else class="portal-analytics-empty">当前周期暂无访问记录。</div>
        </template>
      </div>
    </section>

    <!-- 案件进度更新：非管理员也可见，故不加 v-if（与旧版一致） -->
    <section class="app-card">
      <div class="app-card-head">
        <h2>案件进度更新</h2>
        <span class="app-card-head-meta">共 {{ progressUpdates.length }} 条</span>
      </div>
      <div class="app-card-body">
        <el-form @submit.prevent="submitProgressUpdate">
          <el-form-item label="标题">
            <el-input v-model="progressForm.title" placeholder="进度标题" maxlength="128" />
          </el-form-item>
          <el-form-item label="内容">
            <el-input v-model="progressForm.body" type="textarea" :rows="3" placeholder="进度内容" maxlength="5000" />
          </el-form-item>
          <el-form-item label="下步计划">
            <el-input v-model="progressForm.next_steps" placeholder="下一步计划（可选）" maxlength="1000" />
          </el-form-item>
          <el-form-item label="可见性">
            <el-select v-model="progressForm.visibility">
              <el-option label="仅内部" value="internal" />
              <el-option label="客户可见" value="client_visible" />
            </el-select>
          </el-form-item>
          <el-button type="primary" :loading="progressLoading" @click="submitProgressUpdate">创建</el-button>
        </el-form>
        <div class="app-card-section">
          <el-table :data="progressUpdates" stripe size="small">
            <el-table-column prop="title" label="标题" show-overflow-tooltip />
            <el-table-column label="可见性" width="100">
              <template #default="{ row }">{{ row.visibility === 'client_visible' ? '客户可见' : '内部' }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <span class="app-tag" :class="progressStatus(row.status).tagClass">{{ progressStatus(row.status).label }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="200">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending_review' && canManagePortal" size="small" text type="primary" @click="publishProgress(row)">审核并发布</el-button>
                <el-button v-if="row.status === 'published' && canManagePortal" size="small" text type="warning" @click="withdrawProgress(row)">撤回</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>

    <!-- 案件成员 -->
    <section v-if="canManagePortal" class="app-card">
      <div class="app-card-head">
        <h2>案件成员</h2>
        <span class="app-card-head-meta">共 {{ caseMembers.length }} 人</span>
      </div>
      <div class="app-card-body">
        <el-table :data="caseMembers" stripe size="small">
          <el-table-column prop="user_id" label="用户ID" width="80" />
          <el-table-column prop="case_role" label="角色" width="140">
            <template #default="{ row }">
              <span class="app-tag" :class="row.case_role === 'owner' ? 'is-info' : ''">{{ caseRoleLabel(row.case_role) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" text type="danger" @click="removeCaseMember(row)">移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>

    <!-- 创建门户链接：标题、字段、placeholder 与提交按钮均保持原样（e2e 锚点） -->
    <el-dialog v-model="showPortalDialog" title="创建客户门户链接" width="520px">
      <el-form :model="portalForm" label-width="100px" size="small">
        <el-form-item label="客户邮箱">
          <el-input v-model="portalForm.client_email" placeholder="客户邮箱（用于验证码）" />
        </el-form-item>
        <el-form-item label="有效期">
          <el-select v-model="portalForm.expires_days" style="width:100%">
            <el-option :label="7" :value="7" />
            <el-option :label="30" :value="30" />
            <el-option :label="90" :value="90" />
          </el-select>
        </el-form-item>
        <el-form-item label="邮箱验证"><span class="app-tag is-ok">强制启用</span></el-form-item>
        <el-form-item label="聚合全部">
          <el-switch v-model="portalForm.aggregate_case" />
          <div class="aggregate-hint">开启后该链接自动展示本案件全部已发布客户可见内容（进度+文书），一个案件一个URL。仅包含已发布内容，未发布项不会暴露；建议发布前对照清单复核客户可见范围。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPortalDialog = false">取消</el-button>
        <el-button type="primary" :loading="portalCreating" @click="createPortalLink">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElMessageBox } from 'element-plus/es/components/message-box/index'
import 'element-plus/es/components/message-box/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElSwitch } from 'element-plus/es/components/switch/index'
import 'element-plus/es/components/switch/style/css'
import { legalWorkspace } from '../../api'
import { useLegalCaseCollaboration } from '../../composables/useLegalCaseCollaboration'
import { formatDate } from '../../composables/useLegalWorkspacePresentation'
import { useAuthStore } from '../../stores/auth'

const props = defineProps({
  organizationId: { type: [Number, String], default: null },
  caseId: { type: [Number, String], default: null },
})

const auth = useAuthStore()
// 门户管理（品牌/链接/分析/成员/进度发布）后端要求组织 admin 或 reviewer（_require_case_manager）。
// 与后端保持同一判据：仅看 legal_role，不看系统角色（ux-audit P1-5）。
const canManagePortal = computed(() => ['admin', 'reviewer'].includes(auth.currentUser?.legal_role))

const {
  portalLinks,
  showPortalDialog,
  portalCreating,
  portalForm,
  progressUpdates,
  progressForm,
  progressLoading,
  caseMembers,
  loadPortalLinks,
  createPortalLink,
  revokePortalLink,
  loadProgressUpdates,
  submitProgressUpdate,
  publishProgress,
  withdrawProgress,
  loadCaseMembers,
  removeCaseMember,
} = useLegalCaseCollaboration({
  client: legalWorkspace,
  message: ElMessage,
  confirm: ElMessageBox.confirm,
  organizationId: computed(() => props.organizationId),
  caseId: computed(() => props.caseId),
})

const branding = reactive({ portal_logo_url: '', portal_welcome_message: '' })
const brandingSaving = ref(false)
const portalAnalytics = ref({ summary: {}, daily: [] })
const analyticsDays = ref(30)
const analyticsLoading = ref(false)

async function loadBranding() {
  if (!props.organizationId) return
  try {
    const res = await legalWorkspace.getPortalBranding(props.organizationId)
    const d = res?.data?.data ?? res?.data ?? res
    branding.portal_logo_url = d.portal_logo_url || ''
    branding.portal_welcome_message = d.portal_welcome_message || ''
  } catch (e) {
    /* 品牌接口失败不影响门户功能 */
  }
}

async function saveBranding() {
  brandingSaving.value = true
  try {
    await legalWorkspace.updatePortalBranding(props.organizationId, {
      portal_logo_url: branding.portal_logo_url || null,
      portal_welcome_message: branding.portal_welcome_message || null,
    })
    ElMessage.success('品牌配置已保存')
  } catch (e) {
    ElMessage.error('保存失败，请重试')
  } finally {
    brandingSaving.value = false
  }
}

async function loadPortalAnalytics() {
  if (!props.organizationId || !props.caseId) return
  analyticsLoading.value = true
  try {
    const { data } = await legalWorkspace.getPortalAnalytics(props.organizationId, props.caseId, analyticsDays.value)
    portalAnalytics.value = data || { summary: {}, daily: [] }
  } catch {
    portalAnalytics.value = { summary: {}, daily: [] }
  } finally {
    analyticsLoading.value = false
  }
}

// 门户链接状态：判据与文案不变，只把 Element 的 type 换成方向 B 的 .app-tag 变体
const PORTAL_STATUS_LABELS = { expired: '已过期', revoked: '已撤销', access_limited: '已达访问上限' }
const portalStatus = (row) => {
  if (row.status !== 'active') {
    return { tagClass: '', label: PORTAL_STATUS_LABELS[row.status] || row.status }
  }
  if (row.expires_at) {
    const ms = new Date(String(row.expires_at)).getTime() - Date.now()
    if (!Number.isNaN(ms) && ms / 86400000 <= 3) return { tagClass: 'is-pending', label: '即将到期' }
  }
  return { tagClass: 'is-ok', label: '生效中' }
}

// 进度状态字典：文案保持原样，配色改为 .app-tag 变体（warning → is-pending）
const PROGRESS_STATUS = {
  draft: { tagClass: '', label: '草稿' },
  pending_review: { tagClass: 'is-pending', label: '待审核' },
  published: { tagClass: 'is-ok', label: '已发布' },
  withdrawn: { tagClass: 'is-pending', label: '已撤回' },
}
const progressStatus = (status) => PROGRESS_STATUS[status] || { tagClass: '', label: status }

const CASE_ROLE_LABELS = { owner: '负责人', collaborator: '协作者', viewer: '只读', client_contact: '客户' }
const caseRoleLabel = (role) => CASE_ROLE_LABELS[role] || role

const portalSummary = computed(() => {
  const active = portalLinks.value.filter((row) => row.status === 'active')
  const expiring = active.filter((row) => {
    if (!row.expires_at) return false
    const remaining = (new Date(String(row.expires_at)).getTime() - Date.now()) / 86400000
    return !Number.isNaN(remaining) && remaining <= 3
  }).length
  const accesses = portalLinks.value.reduce((sum, row) => sum + Number(row.access_count || 0), 0)
  const latest = portalLinks.value
    .map((row) => row.last_accessed_at)
    .filter(Boolean)
    .sort((a, b) => new Date(String(b)).getTime() - new Date(String(a)).getTime())[0]
  return { active: active.length, expiring, accesses, lastAccess: latest ? formatDate(latest) : '尚未访问' }
})

onMounted(() => {
  if (canManagePortal.value) {
    loadPortalLinks()
    loadCaseMembers()
    loadBranding()
    loadPortalAnalytics()
  }
  loadProgressUpdates()
})
watch(
  () => [props.organizationId, props.caseId],
  () => {
    if (canManagePortal.value) {
      loadPortalLinks()
      loadCaseMembers()
      loadBranding()
      loadPortalAnalytics()
    }
    loadProgressUpdates()
  },
)

const barHeight = (value) => {
  const max = Math.max(...(portalAnalytics.value.daily || []).map((point) => Number(point.visits || 0)), 1)
  return Math.max(8, Math.round((Number(value || 0) / max) * 100))
}
</script>

<style scoped>
/* 版式骨架取自 design-tokens.css 的 .app-*（卡片/页头/读数卡/标签），
   这里只补本 tab 的排布与图表细节；颜色、圆角、字号、间距一律走令牌，无硬编码色值。 */
.portal-workspace {
  display: grid;
  gap: var(--space-6);
}

/* 权限不足时的说明卡：居中排布，沿用 .app-card 皮肤 */
.portal-permission-card {
  display: grid;
  justify-items: center;
  gap: var(--space-2);
}

.portal-permission-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

/* 「最近访问」是日期而非计数，读数卡里降一档字号，避免 28px 挤断 */
.portal-stat-text {
  font-size: var(--text-xl);
  font-weight: 600;
  letter-spacing: 0;
}

/* 访问分析：卡头右侧的原生周期选择器（保留原生 select，行为不变） */
.portal-period-select {
  flex: 0 0 auto;
  height: 30px;
  padding: 0 var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.portal-period-select:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}

.portal-period-select:disabled {
  color: var(--color-text-muted);
  cursor: wait;
}

.analytics-body {
  display: grid;
  gap: var(--space-5);
}

.analytics-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.analytics-metrics > div {
  display: grid;
  gap: var(--space-1);
}

.analytics-metrics strong {
  color: var(--color-text);
  font-size: var(--text-xl);
  font-weight: 600;
}

.analytics-chart {
  display: flex;
  align-items: flex-end;
  gap: 5px;
  min-height: 150px;
  padding: var(--space-3) var(--space-1) 0;
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
}

.analytics-day {
  display: grid;
  flex: 1 0 22px;
  align-items: end;
  justify-items: center;
  gap: var(--space-1);
  min-width: 22px;
  height: 128px;
}

.analytics-day strong {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 500;
}

.analytics-day time {
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.analytics-bar-wrap {
  display: flex;
  align-items: flex-end;
  width: 10px;
  height: 82px;
  border-radius: var(--radius-xs);
  background: var(--color-surface-hover);
}

.analytics-bar-wrap i {
  display: block;
  width: 100%;
  min-height: 3px;
  border-radius: var(--radius-xs);
  background: var(--color-primary);
}

.portal-analytics-empty {
  padding: var(--space-4) 0;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

/* 弹窗内「聚合全部」的补充说明 */
.aggregate-hint {
  margin-top: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

@media (max-width: 720px) {
  .portal-analytics-head {
    flex-wrap: wrap;
  }

  .portal-analytics-head .app-card-head-meta {
    margin-left: 0;
  }

  .analytics-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
