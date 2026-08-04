<template>
  <div class="legal-portal">
    <div v-if="errorState" class="portal-error">
      <el-card shadow="never">
        <el-empty :description="errorState" :image-size="80" />
      </el-card>
    </div>

    <div v-else-if="step === 'otp'" class="portal-otp">
      <el-card shadow="never">
        <template #header><span class="card-title">案件查询验证</span></template>
        <el-alert v-if="maskedEmail" type="info" :closable="false" show-icon style="margin-bottom:16px">
          <template #title>验证码已发送至 {{ maskedEmail }}</template>
        </el-alert>
        <el-form @submit.prevent="verifyOtp">
          <el-form-item label="6位验证码">
            <el-input v-model="otpCode" maxlength="6" placeholder="请输入验证码" style="width:240px" @input="otpCode = otpCode.replace(/\D/g, '')" />
          </el-form-item>
          <div class="otp-actions">
            <el-button type="primary" :loading="verifyLoading" @click="verifyOtp">验证</el-button>
            <el-button :loading="resendLoading" :disabled="resendCooldown > 0" @click="resendOtp">
              {{ resendCooldown > 0 ? `${resendCooldown}s 后重发` : '重新发送' }}
            </el-button>
          </div>
        </el-form>
      </el-card>
    </div>

    <div v-else-if="step === 'content'" class="portal-content">
      <el-card shadow="never">
        <template #header>
          <div class="result-header">
            <span class="card-title">案件进展</span>
          </div>
        </template>
        <div v-if="progressUpdates.length">
          <div v-for="u in progressUpdates" :key="u.id" class="progress-item">
            <div class="progress-header">
              <strong>{{ u.title }}</strong>
              <span class="muted">{{ formatDate(u.published_at || u.created_at) }}</span>
            </div>
            <p class="progress-body">{{ u.body }}</p>
            <div v-if="u.next_steps" class="progress-next">
              <strong>后续步骤：</strong>
              <p>{{ u.next_steps }}</p>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无进展更新" :image-size="60" />
      </el-card>

      <el-card v-if="publishedDocs.length" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">公开文档</span></template>
        <el-table :data="publishedDocs" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="title" label="文档名称" show-overflow-tooltip />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="downloadDoc(row)">下载</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="portalContent.invoice" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">账单摘要</span></template>
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item label="账单号">{{ portalContent.invoice.invoice_number }}</el-descriptions-item>
          <el-descriptions-item label="金额">¥{{ portalContent.invoice.total_amount }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ invoiceStatusLabel(portalContent.invoice.status) }}</el-descriptions-item>
          <el-descriptions-item label="账期">{{ portalContent.invoice.period_start }} ~ {{ portalContent.invoice.period_end }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card v-if="portalContent.sign_requests?.length" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">签署请求</span></template>
        <el-table :data="portalContent.sign_requests" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="title" label="文档" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'pending' ? 'warning' : 'success'" size="small">{{ row.status === 'pending' ? '待签署' : '已签署' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button v-if="row.status === 'pending'" size="small" text type="primary" @click="openSignLink(row)">前往签署</el-button>
              <span v-else class="muted">已完成</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'

const route = useRoute()
const token = route.params.token

const step = ref('otp')
const errorState = ref('')
const maskedEmail = ref('')
const otpCode = ref('')
const verifyLoading = ref(false)
const resendLoading = ref(false)
const resendCooldown = ref(0)
let cooldownTimer = null

const portalContent = ref({})
const portalSession = ref(sessionStorage.getItem(`portal-session:${token}`) || '')
const progressUpdates = ref([])
const publishedDocs = ref([])

const initOtp = async () => {
  try {
    const { data } = await api.portalSendOtp(token)
    maskedEmail.value = data.masked_email || ''
    startCooldown()
  } catch (e) {
    const detail = e.response?.data?.detail
    if (e.response?.status === 404) errorState.value = '链接不存在'
    else if (e.response?.status === 410) errorState.value = '链接已过期'
    else if (e.response?.status === 403) errorState.value = '链接已被撤销'
    else errorState.value = detail || '无法访问'
  }
}

const verifyOtp = async () => {
  if (otpCode.value.length !== 6) return ElMessage.warning('请输入6位验证码')
  verifyLoading.value = true
  try {
    const { data } = await api.portalVerifyOtp(token, otpCode.value)
    portalSession.value = data.session_token
    sessionStorage.setItem(`portal-session:${token}`, data.session_token)
    await loadContent()
    step.value = 'content'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '验证失败')
  }
  verifyLoading.value = false
}

const resendOtp = async () => {
  resendLoading.value = true
  try {
    const { data } = await api.portalSendOtp(token)
    maskedEmail.value = data.masked_email || maskedEmail.value
    startCooldown()
    ElMessage.success('验证码已重新发送')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  }
  resendLoading.value = false
}

const startCooldown = () => {
  resendCooldown.value = 60
  if (cooldownTimer) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    resendCooldown.value--
    if (resendCooldown.value <= 0) clearInterval(cooldownTimer)
  }, 1000)
}

const loadContent = async () => {
  try {
    const { data } = await api.portalGetContent(token, portalSession.value)
    portalContent.value = data
    progressUpdates.value = data.progress_updates || []
    publishedDocs.value = data.documents || []
  } catch (e) {
    errorState.value = e.response?.data?.detail || '加载内容失败'
  }
}

const downloadDoc = async (doc) => {
  try {
    const { data } = await api.portalDownloadDocument(token, doc.id, portalSession.value)
    const url = URL.createObjectURL(data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = doc.title || 'document'
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '文件暂不可下载')
  }
}

const openSignLink = (req) => {
  if (req.sign_url) {
    window.open(req.sign_url, '_blank')
  } else {
    ElMessage.info('暂无签署链接')
  }
}

const invoiceStatusLabel = (s) => ({ draft: '草稿', sent: '已发送', paid: '已支付', overdue: '逾期', voided: '已作废' }[s] || s)
const formatDate = (v) => {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

onMounted(() => {
  if (!token) {
    errorState.value = '链接无效'
    return
  }
  initOtp()
})

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>

<style scoped>
.legal-portal {
  max-width: 800px;
  margin: 40px auto;
  min-height: 60vh;
}

.card-title {
  font-weight: 700;
  font-size: 15px;
}

.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.portal-error {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 40vh;
}

.otp-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.progress-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.progress-item:last-child {
  border-bottom: none;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.progress-body {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  margin: 0;
}

.progress-next {
  margin-top: 8px;
  font-size: 13px;
}

.progress-next p {
  margin: 4px 0 0;
  line-height: 1.6;
}
</style>
