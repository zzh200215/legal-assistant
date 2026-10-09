<template>
  <div class="tab-panel">
    <el-card shadow="never">
      <template #header>
        <div class="result-header">
          <span class="card-title">客户门户品牌</span>
          <el-button size="small" :loading="brandingSaving" @click="saveBranding">保存品牌配置</el-button>
        </div>
      </template>
      <el-form label-width="120px" label-position="left">
        <el-form-item label="律所 Logo URL">
          <el-input v-model="branding.portal_logo_url" placeholder="https://... 图片直链（可选）" maxlength="512" clearable />
        </el-form-item>
        <el-form-item label="欢迎语">
          <el-input v-model="branding.portal_welcome_message" placeholder="客户打开门户时展示的欢迎语（可选）" maxlength="256" clearable />
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="result-header">
          <span class="card-title">客户门户链接</span>
          <el-button size="small" type="primary" @click="showPortalDialog = true">创建门户链接</el-button>
        </div>
      </template>
      <div class="portal-operations-summary" aria-label="门户运营摘要">
        <div><span>生效中</span><strong>{{ portalSummary.active }}</strong></div>
        <div><span>3 天内到期</span><strong>{{ portalSummary.expiring }}</strong></div>
        <div><span>累计访问</span><strong>{{ portalSummary.accesses }}</strong></div>
        <div><span>最近访问</span><strong>{{ portalSummary.lastAccess }}</strong></div>
      </div>
      <el-table :data="portalLinks" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="token_prefix" label="令牌前缀" width="120" />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="row.aggregate_case ? 'primary' : 'info'" size="small" effect="plain">
              {{ row.aggregate_case ? '聚合' : '定向' }}
            </el-tag>
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
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="portalStatus(row).type" size="small">{{ portalStatus(row).label }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" size="small" type="danger" @click="revokePortalLink(row)">撤销</el-button>
            <el-tag v-else type="info" size="small">已失效</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <section class="portal-analytics" aria-label="门户访问分析">
      <header class="portal-analytics-heading">
        <div><h3>访问分析</h3><span>仅显示当前案件的聚合访问数据</span></div>
        <select v-model="analyticsDays" aria-label="访问分析周期" :disabled="analyticsLoading" @change="loadPortalAnalytics">
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
      </header>
      <div v-if="analyticsLoading" class="portal-analytics-empty">正在加载访问数据…</div>
      <template v-else>
        <div class="analytics-metrics">
          <div><span>成功访问</span><strong>{{ portalAnalytics.summary?.visits || 0 }}</strong></div>
          <div><span>去重访客</span><strong>{{ portalAnalytics.summary?.unique_visitors || 0 }}</strong></div>
          <div><span>活跃链接</span><strong>{{ portalAnalytics.summary?.active_links || 0 }}</strong></div>
          <div><span>被拒访问</span><strong>{{ portalAnalytics.summary?.denied || 0 }}</strong></div>
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
    </section>

    <el-card shadow="never" style="margin-top:20px">
      <template #header><span class="card-title">案件进度更新</span></template>
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
      <el-table :data="progressUpdates" stripe size="small" style="margin-top:16px">
        <el-table-column prop="title" label="标题" show-overflow-tooltip />
        <el-table-column label="可见性" width="100">
          <template #default="{ row }">{{ row.visibility === 'client_visible' ? '客户可见' : '内部' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="{ draft: 'info', pending_review: 'warning', published: 'success', withdrawn: 'warning' }[row.status] || 'info'" size="small">{{ { draft: '草稿', pending_review: '待审核', published: '已发布', withdrawn: '已撤回' }[row.status] || row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.status === 'pending_review'" size="small" type="primary" @click="publishProgress(row)">审核并发布</el-button>
            <el-button v-if="row.status === 'published'" size="small" type="warning" @click="withdrawProgress(row)">撤回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" style="margin-top:20px">
      <template #header><span class="card-title">案件成员</span></template>
      <el-table :data="caseMembers" stripe size="small">
        <el-table-column prop="user_id" label="用户ID" width="80" />
        <el-table-column prop="case_role" label="角色" width="140">
          <template #default="{ row }">
            <el-tag size="small">{{ { owner: '负责人', collaborator: '协作者', viewer: '只读', client_contact: '客户' }[row.case_role] || row.case_role }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="removeCaseMember(row)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

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
        <el-form-item label="邮箱验证"><el-tag type="success">强制启用</el-tag></el-form-item>
        <el-form-item label="聚合全部">
          <el-switch v-model="portalForm.aggregate_case" />
          <div class="aggregate-hint">开启后该链接自动展示本案件全部已发布客户可见内容（进度+文书），一个案件一个URL</div>
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
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElSwitch } from 'element-plus/es/components/switch/index'
import 'element-plus/es/components/switch/style/css'
import { legalWorkspace } from '../../api'
import { useLegalCaseCollaboration } from '../../composables/useLegalCaseCollaboration'
import { formatDate } from '../../composables/useLegalWorkspacePresentation'

const props = defineProps({
  organizationId: { type: [Number, String], default: null },
  caseId: { type: [Number, String], default: null },
})

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

const portalStatus = (row) => {
  if (row.status !== 'active') {
    const labels = { expired: '已过期', revoked: '已撤销', access_limited: '已达访问上限' }
    return { type: 'info', label: labels[row.status] || row.status }
  }
  if (row.expires_at) {
    const ms = new Date(String(row.expires_at)).getTime() - Date.now()
    if (!Number.isNaN(ms) && ms / 86400000 <= 3) return { type: 'warning', label: '即将到期' }
  }
  return { type: 'success', label: '生效中' }
}

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
  loadPortalLinks()
  loadProgressUpdates()
  loadCaseMembers()
  loadBranding()
  loadPortalAnalytics()
})
watch(
  () => [props.organizationId, props.caseId],
  () => {
    loadPortalLinks()
    loadProgressUpdates()
    loadCaseMembers()
    loadBranding()
    loadPortalAnalytics()
  },
)

const barHeight = (value) => {
  const max = Math.max(...(portalAnalytics.value.daily || []).map((point) => Number(point.visits || 0)), 1)
  return Math.max(8, Math.round((Number(value || 0) / max) * 100))
}
</script>

<style scoped>
.result-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.card-title {
  font-weight: 700;
  font-size: 15px;
}
.tab-panel {
  display: grid;
  gap: 20px;
}
.portal-operations-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; margin: 0 0 14px; padding: 12px 0 14px; border-bottom: 1px solid var(--color-border-light); }
.portal-operations-summary div { display: grid; gap: 4px; }
.portal-operations-summary span { color: var(--color-text-muted); font-size: 11px; }
.portal-operations-summary strong { color: var(--color-text); font-size: 18px; font-weight: 600; }
.portal-analytics { display: grid; gap: 16px; margin-top: 20px; padding: 18px 0 4px; border-top: 1px solid var(--color-border); }
.portal-analytics-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.portal-analytics-heading h3 { margin: 0; color: var(--color-text); font-size: 14px; font-weight: 600; }
.portal-analytics-heading span { display: block; margin-top: 4px; color: var(--color-text-muted); font-size: 11px; }
.portal-analytics-heading select { min-height: 30px; padding: 4px 8px; border: 1px solid var(--color-border); background: #fff; color: var(--color-text-secondary); font-size: 12px; }
.analytics-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; }
.analytics-metrics div { display: grid; gap: 4px; }
.analytics-metrics span { color: var(--color-text-muted); font-size: 11px; }
.analytics-metrics strong { color: var(--color-text); font-size: 18px; font-weight: 600; }
.analytics-chart { display: flex; align-items: end; gap: 5px; min-height: 150px; padding: 12px 4px 0; border-bottom: 1px solid var(--color-border); overflow-x: auto; }
.analytics-day { display: grid; flex: 1 0 22px; align-items: end; justify-items: center; gap: 4px; min-width: 22px; height: 128px; }
.analytics-day strong { color: var(--color-text-secondary); font-size: 10px; font-weight: 500; }
.analytics-day time { color: var(--color-text-muted); font-family: var(--font-mono); font-size: 9px; white-space: nowrap; }
.analytics-bar-wrap { display: flex; align-items: end; width: 10px; height: 82px; background: var(--color-surface-hover); }
.analytics-bar-wrap i { display: block; width: 100%; min-height: 3px; background: var(--color-primary); }
.portal-analytics-empty { padding: 24px 0; color: var(--color-text-muted); font-size: 12px; }
.aggregate-hint {
  font-size: 12px;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin-top: 4px;
}
@media (max-width: 720px) { .portal-operations-summary, .analytics-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; } .portal-analytics-heading { flex-direction: column; } }
</style>
