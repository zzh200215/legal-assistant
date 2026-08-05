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
      <el-table :data="portalLinks" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="token_prefix" label="令牌前缀" width="120" />
        <el-table-column label="有效期">
          <template #default="{ row }">
            <span>{{ formatDate(row.expires_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="access_count" label="访问次数" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
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
      </el-form>
      <template #footer>
        <el-button @click="showPortalDialog = false">取消</el-button>
        <el-button type="primary" :loading="portalCreating" @click="createPortalLink">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElOption, ElSelect } from 'element-plus/es/components/select/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
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
  organizationId: props.organizationId,
  caseId: props.caseId,
})

const branding = reactive({ portal_logo_url: '', portal_welcome_message: '' })
const brandingSaving = ref(false)

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

onMounted(() => {
  loadPortalLinks()
  loadProgressUpdates()
  loadCaseMembers()
  loadBranding()
})
watch(
  () => [props.organizationId, props.caseId],
  () => {
    loadPortalLinks()
    loadProgressUpdates()
    loadCaseMembers()
    loadBranding()
  },
)
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
</style>
