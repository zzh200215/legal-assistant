<template>
  <div class="legal-contracts">
    <!-- 方向 B：单卡片台账 —— 卡片头（标题 / 数量 / 操作）+ 卡片体表格；表格与弹窗行为不变 -->
    <section class="app-card">
      <div class="app-card-head">
        <h2>合同列表</h2>
        <span class="app-card-head-meta">{{ total }} 份</span>
        <div class="contract-actions">
          <el-button size="small" :loading="loading" @click="loadContracts">刷新</el-button>
          <el-button size="small" type="primary" @click="createVisible = true">新建合同</el-button>
        </div>
      </div>
      <div class="app-card-body">
        <div class="contract-filters" aria-label="合同台账筛选">
          <select v-model="filters.review_status" aria-label="按审查状态筛选">
            <option value="">全部审查状态</option>
            <option value="draft">草稿</option>
            <option value="pending_review">待审核</option>
            <option value="needs_lawyer_review">需律师审核</option>
            <option value="lawyer_approved">律师通过</option>
            <option value="returned_for_facts">退回补充</option>
          </select>
          <select v-model="filters.risk" aria-label="按风险筛选">
            <option value="">全部风险</option>
            <option value="high">有高风险</option>
            <option value="unanchored">有无定位风险</option>
            <option value="none">未审查</option>
          </select>
        </div>
        <el-table v-loading="loading" :data="contracts" stripe size="small">
          <el-table-column prop="contract_no" label="合同编号" width="150" />
          <el-table-column prop="title" label="合同名称" min-width="180" show-overflow-tooltip />
          <el-table-column prop="counterparty" label="相对方" min-width="140" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column label="关联案件" width="100">
            <template #default="{ row }">{{ row.case_id ? `#${row.case_id}` : '未关联' }}</template>
          </el-table-column>
          <el-table-column label="最近审查" width="120">
            <template #default="{ row }">
              <span v-if="row.review" class="app-tag" :class="reviewTagClass(row.review)">{{ reviewStatusText(row.review) }}</span>
              <span v-else class="muted">未审查</span>
            </template>
          </el-table-column>
          <el-table-column label="风险" width="150">
            <template #default="{ row }">
              <span v-if="row.review">
                {{ row.review.risks_total }} 条
                <span v-if="row.review.risks_high" class="risk-high"> · 高 {{ row.review.risks_high }}</span>
                <span v-if="row.review.risks_unanchored" class="risk-gap"> · 无定位 {{ row.review.risks_unanchored }}</span>
              </span>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="230">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="openVersions(row)">版本</el-button>
              <el-button size="small" text @click="openMilestones(row)">关键节点</el-button>
              <el-button size="small" text @click="openDiff(row)">版本对比</el-button>
              <el-tooltip v-if="!signingEnabled" content="电子签署服务未配置（试点暂不开放）" placement="top">
                <span><el-button size="small" text type="info" disabled>签署</el-button></span>
              </el-tooltip>
              <el-button v-else size="small" text type="success" @click="openSigning(row)">签署</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </section>

    <el-dialog v-model="createVisible" title="新建合同" width="560px">
      <el-form :model="createForm" label-width="86px">
        <el-form-item label="合同名称"><el-input v-model="createForm.title" maxlength="256" /></el-form-item>
        <el-form-item label="相对方"><el-input v-model="createForm.counterparty" maxlength="256" /></el-form-item>
        <el-form-item label="合同类型"><el-input v-model="createForm.contract_type" /></el-form-item>
        <el-form-item label="合同编号"><el-input v-model="createForm.contract_no" placeholder="留空自动生成" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="createContract">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="versionsVisible" :title="`${selected?.title || ''} - 版本`" width="760px">
      <div class="version-create">
        <el-input v-model="versionText" type="textarea" :rows="4" placeholder="粘贴本次合同文本快照" />
        <el-button type="primary" :disabled="!versionText.trim()" :loading="saving" @click="createVersion">保存新版本</el-button>
      </div>
      <el-table :data="versions" size="small" class="dialog-table">
        <el-table-column prop="version_no" label="版本" width="80" />
        <el-table-column prop="parse_status" label="解析状态" width="130" />
        <el-table-column prop="version_note" label="说明" show-overflow-tooltip />
        <el-table-column prop="created_at" label="创建时间" width="170" />
      </el-table>
    </el-dialog>

    <el-dialog v-model="diffVisible" :title="`${selected?.title || ''} - 版本对比`" width="760px">
      <div class="diff-controls">
        <el-select v-model="diffBase" placeholder="基准版本"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.version_no" /></el-select>
        <el-select v-model="diffTarget" placeholder="目标版本"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.version_no" /></el-select>
        <el-button type="primary" :disabled="!diffBase || !diffTarget || diffBase === diffTarget" @click="loadDiff">对比</el-button>
      </div>
      <el-empty v-if="!diffResult" description="请选择两个版本" />
      <pre v-else class="diff-result">{{ JSON.stringify(diffResult, null, 2) }}</pre>
    </el-dialog>

    <el-dialog v-model="milestonesVisible" :title="`${selected?.title || ''} - 关键节点`" width="680px">
      <el-table :data="milestones" size="small"><el-table-column prop="milestone_type" label="类型" /><el-table-column prop="standard_date" label="日期" /><el-table-column prop="status" label="状态" /></el-table>
    </el-dialog>

    <el-dialog v-model="signingVisible" :title="`${selected?.title || ''} - 电子签署`" width="680px">
      <el-form :model="signForm" label-width="120px">
        <el-form-item label="合同版本"><el-select v-model="signForm.contract_version_id" class="signing-version"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.id" /></el-select></el-form-item>
        <el-alert title="签署服务由组织配置的法大大沙箱提供" type="info" :closable="false" show-icon class="signing-alert" />
        <el-form-item label="签署方姓名"><el-input v-model="signForm.party_name" maxlength="128" /></el-form-item>
        <el-form-item label="已核验手机号"><el-input v-model="signForm.phone_masked" placeholder="例如 138****0000" maxlength="32" /></el-form-item>
      </el-form>
      <el-table :data="signRequests" size="small" class="dialog-table"><el-table-column prop="provider" label="服务商" /><el-table-column prop="provider_request_id" label="签署单号" /><el-table-column prop="status" label="状态" /></el-table>
      <template #footer><el-button @click="signingVisible = false">关闭</el-button><el-button type="primary" :loading="saving" @click="createAndSendSignRequest">创建并发送</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTooltip } from 'element-plus/es/components/tooltip/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tooltip/style/css'
import api from '../api'

const props = defineProps({ orgId: { type: Number, required: true }, caseId: { type: Number, required: false, default: null } })
const contracts = ref([])
const total = ref(0)
const filters = ref({ review_status: '', risk: '' }); const REVIEW_STATUS_LABELS = {
  draft: '草稿', pending_review: '待审核', needs_lawyer_review: '需律师审核', needs_facts: '待补充事实',
  lawyer_approved: '律师通过', returned_for_facts: '退回补充', offline_consultation: '转线下', archived: '已归档',
}
const reviewStatusText = (review) => (review ? REVIEW_STATUS_LABELS[review.status] || review.status : '未审查')
const reviewTagClass = (review) => {
  if (!review) return ''
  if (review.status === 'lawyer_approved') return 'is-ok'
  if (review.status === 'returned_for_facts') return 'is-danger'
  return 'is-warning'
}
const loading = ref(false); const saving = ref(false)
const signingEnabled = ref(false)
const createVisible = ref(false); const versionsVisible = ref(false); const diffVisible = ref(false); const milestonesVisible = ref(false); const signingVisible = ref(false)
const selected = ref(null); const versions = ref([]); const milestones = ref([]); const versionText = ref(''); const diffBase = ref(null); const diffTarget = ref(null); const diffResult = ref(null)
const createForm = ref({ title: '', counterparty: '', contract_type: '', contract_no: '' })
const signRequests = ref([]); const signForm = ref({ contract_version_id: null, party_name: '', phone_masked: '' })

const loadContracts = async () => {
  if (!props.orgId) return
  loading.value = true
  try {
    const { data } = await api.listLegalContracts(props.orgId, props.caseId, {
      review_status: filters.value.review_status,
      risk: filters.value.risk,
    })
    contracts.value = data.items || []
    total.value = data.total ?? contracts.value.length
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '合同加载失败')
  } finally {
    loading.value = false
  }
}
const createContract = async () => { if (!createForm.value.title.trim()) return ElMessage.warning('请输入合同名称'); saving.value = true; try { await api.createLegalContract(props.orgId, { ...createForm.value, case_id: props.caseId || null, contract_no: createForm.value.contract_no || null }); createVisible.value = false; createForm.value = { title: '', counterparty: '', contract_type: '', contract_no: '' }; await loadContracts() } catch (e) { ElMessage.error(e.response?.data?.detail || '合同创建失败') } finally { saving.value = false } }
const loadVersions = async (contract) => { selected.value = contract; const { data } = await api.listLegalContractVersions(contract.id); versions.value = data || [] }
const openVersions = async (contract) => { try { await loadVersions(contract); versionsVisible.value = true } catch { ElMessage.error('版本加载失败') } }
const createVersion = async () => { saving.value = true; try { await api.createLegalContractVersion(selected.value.id, { source_type: 'text_snapshot', text_snapshot: versionText.value, version_note: '工作台新增版本' }); versionText.value = ''; await loadVersions(selected.value) } catch (e) { ElMessage.error(e.response?.data?.detail || '版本保存失败') } finally { saving.value = false } }
const openDiff = async (contract) => { try { await loadVersions(contract); diffBase.value = null; diffTarget.value = null; diffResult.value = null; diffVisible.value = true } catch { ElMessage.error('版本加载失败') } }
const loadDiff = async () => { try { const { data } = await api.diffLegalContractVersions(selected.value.id, diffBase.value, diffTarget.value); diffResult.value = data } catch (e) { ElMessage.error(e.response?.data?.detail || 'Diff 生成失败') } }
const openMilestones = async (contract) => { selected.value = contract; try { const { data } = await api.listContractMilestones(contract.id); milestones.value = data || []; milestonesVisible.value = true } catch { ElMessage.error('关键节点加载失败') } }
const openSigning = async (contract) => { try { await loadVersions(contract); const { data } = await api.listSignRequests(contract.id); signRequests.value = data || []; signForm.value = { contract_version_id: versions.value[0]?.id || null, party_name: '', phone_masked: '' }; signingVisible.value = true } catch { ElMessage.error('签署记录加载失败') } }
const createAndSendSignRequest = async () => { if (!signForm.value.contract_version_id || !signForm.value.party_name.trim() || !signForm.value.phone_masked.trim()) return ElMessage.warning('请填写版本、签署方和已核验手机号'); saving.value = true; try { const { data } = await api.createSignRequest(selected.value.id, { contract_version_id: signForm.value.contract_version_id, parties: [{ name: signForm.value.party_name.trim(), phone_masked: signForm.value.phone_masked.trim(), sign_order: 1 }] }); await api.sendSignRequest(data.id); const result = await api.listSignRequests(selected.value.id); signRequests.value = result.data || []; ElMessage.success('签署请求已提交给服务商') } catch (e) { ElMessage.error(e.response?.data?.detail || '签署请求创建失败') } finally { saving.value = false } }
const loadFeatures = async () => { try { const { data } = await api.getFeatureFlags(); signingEnabled.value = !!data.signing_enabled } catch { /* 特性开关不可用时保持默认关闭 */ } }
watch(() => [props.orgId, props.caseId], loadContracts, { immediate: true })
watch(filters, loadContracts, { deep: true })
onMounted(loadFeatures)
</script>

<style scoped>
/* 方向 B：间距/圆角/色板一律走 design-tokens 令牌，卡片头与表格皮肤由 .app-card 体系提供 */
.legal-contracts { display: grid; gap: var(--space-4); min-width: 0; }
.contract-actions { display: flex; align-items: center; gap: var(--space-2); }
.dialog-table { margin-top: var(--space-4); }
.version-create { display: flex; align-items: flex-start; gap: var(--space-3); }
.version-create .el-textarea { flex: 1; }
.diff-controls { display: flex; align-items: center; flex-wrap: wrap; gap: var(--space-2); margin-bottom: var(--space-4); }
.diff-controls .el-select { width: 160px; }
.diff-result { max-height: 360px; margin: 0; overflow: auto; padding: var(--space-4); border: 1px solid var(--color-border-light); border-radius: var(--radius-lg); background: var(--color-surface-subtle); color: var(--color-text-secondary); font-family: var(--font-mono); font-size: var(--text-xs); line-height: 1.7; white-space: pre-wrap; }
.signing-version { width: 100%; }
.signing-alert { margin-bottom: var(--space-4); }

.risk-high { color: var(--color-danger); }
.risk-gap { color: var(--color-warning); }

.contract-filters { display: flex; align-items: center; gap: var(--space-2); padding-bottom: var(--space-3); }
.contract-filters select { height: 30px; padding: 0 8px; border: 1px solid var(--color-border); border-radius: var(--radius-sm); background: var(--color-bg-elevated); color: var(--color-text-primary); font-size: var(--text-xs); }
</style>
