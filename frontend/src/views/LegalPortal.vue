<template>
  <div class="legal-portal">
    <!-- 公开路由无应用外壳，品牌标识由门户自己承担（34px 品牌方块是全站唯一允许的渐变用法） -->
    <header class="portal-masthead">
      <div class="portal-masthead-brand">
        <span class="portal-logo-mark" aria-hidden="true">律</span>
        <span class="portal-masthead-text">
          <span class="portal-masthead-name">律智检</span>
          <span class="portal-masthead-sub">客户案件门户</span>
        </span>
      </div>
      <!-- 机构身份：内容步取门户品牌数据；验证码步 portalContent 仍为空，自然不渲染 -->
      <div
        v-if="portalContent.organization && (portalContent.organization.portal_logo_url || portalContent.organization.name)"
        class="portal-masthead-org"
      >
        <img
          v-if="portalContent.organization.portal_logo_url"
          class="portal-org-logo"
          :src="portalContent.organization.portal_logo_url"
          alt="机构标识"
        />
        <span v-if="portalContent.organization.name" class="portal-org-name">{{ portalContent.organization.name }}</span>
      </div>
    </header>

    <section v-if="errorState" class="app-card portal-state-card">
      <div class="app-card-body portal-error">
        <el-empty :description="errorState" :image-size="80" />
        <p class="portal-error-hint">请联系您的律师重新生成门户链接。</p>
      </div>
    </section>

    <template v-else-if="step === 'otp'">
      <div class="app-page-head portal-page-head">
        <div class="app-page-head-main">
          <h1 class="app-page-title">案件查询验证</h1>
          <p class="app-page-sub">为保护委托信息，请输入我们发送到您邮箱的 6 位验证码。</p>
        </div>
        <div v-if="organizationName" class="app-page-head-actions">
          <span class="app-tag is-info">{{ organizationName }}</span>
        </div>
      </div>

      <section class="app-card">
        <div class="app-card-head">
          <h2>邮箱验证码</h2>
        </div>
        <div class="app-card-body">
          <el-alert v-if="otpError" type="error" :closable="false" show-icon class="otp-error-alert">
            <template #title>{{ otpError }}</template>
            <p class="otp-error-hint">请稍后点击下方"重新发送"重试；若多次失败，请联系您的律师协助。</p>
          </el-alert>
          <el-alert v-else-if="maskedEmail" type="info" :closable="false" show-icon class="otp-sent-alert">
            <template #title>验证码已发送至 {{ maskedEmail }}</template>
          </el-alert>
          <el-form label-position="top" @submit.prevent="verifyOtp">
            <el-form-item label="6位验证码">
              <el-input v-model="otpCode" maxlength="6" placeholder="输入邮件中的 6 位数字验证码" class="otp-input" @input="otpCode = otpCode.replace(/\D/g, '')" />
            </el-form-item>
            <div class="otp-actions">
              <el-button type="primary" :loading="verifyLoading" @click="verifyOtp">验证</el-button>
              <el-button :loading="resendLoading" :disabled="resendCooldown > 0" @click="resendOtp">
                {{ resendCooldown > 0 ? `${resendCooldown}s 后重发` : '重新发送' }}
              </el-button>
            </div>
          </el-form>
        </div>
      </section>
    </template>

    <template v-else-if="step === 'content'">
      <div class="app-page-head portal-page-head">
        <div class="app-page-head-main">
          <h1 class="app-page-title">{{ portalContent.organization?.portal_welcome_message || '案件进展' }}</h1>
          <p class="app-page-sub">律师为您同步的案件进展与交付物，仅供查看。</p>
        </div>
        <div class="app-page-head-actions">
          <span class="app-tag is-ok">邮箱已验证</span>
        </div>
      </div>

      <section v-if="portalContent.case" class="app-card portal-case-card">
        <div class="app-card-head">
          <h2>案件概览</h2>
          <span class="app-tag is-info">{{ caseStatusLabel(portalContent.case.status) }}</span>
        </div>
        <div class="app-card-body portal-case-body">
          <p class="portal-case-title">{{ portalContent.case.title }}</p>
          <p class="muted">最近更新：{{ formatDate(portalContent.case.updated_at) || '—' }}</p>
        </div>
      </section>

      <section class="app-card">
        <div class="app-card-head">
          <h2>进展更新</h2>
          <span class="app-card-head-meta">{{ progressUpdates.length ? `共 ${progressUpdates.length} 条` : '暂无更新' }}</span>
        </div>
        <div class="app-card-body">
          <ol v-if="progressUpdates.length" class="app-timeline">
            <li v-for="u in progressUpdates" :key="u.id" class="app-timeline-item" :class="{ 'is-done': u.status === 'published' }">
              <span class="app-timeline-dot" aria-hidden="true"></span>
              <div class="timeline-item-body">
                <div class="progress-header">
                  <strong class="progress-title">{{ u.title }}</strong>
                  <div class="progress-meta">
                    <span v-if="u.status === 'published'" class="app-tag is-ok">已发布</span>
                    <span class="muted">{{ formatDate(u.published_at || u.created_at) }}</span>
                  </div>
                </div>
                <p class="progress-body">{{ u.body }}</p>
                <div v-if="u.next_steps" class="progress-next">
                  <strong>后续步骤：</strong>
                  <p>{{ u.next_steps }}</p>
                </div>
              </div>
            </li>
          </ol>
          <p v-else class="app-empty-note">暂无进展更新</p>
        </div>
      </section>

      <section v-if="publishedDocs.length" class="app-card">
        <div class="app-card-head">
          <h2>公开文档</h2>
          <span class="app-card-head-meta">共 {{ publishedDocs.length }} 份</span>
        </div>
        <div class="app-card-body">
          <!-- 桌面表格 / 移动端卡片行（375px 专项 M-10）：窄屏表格截断依赖 tooltip，触屏不可用 -->
          <el-table class="desktop-table" :data="publishedDocs" stripe size="small">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="title" label="文档名称" show-overflow-tooltip />
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button size="small" text type="primary" @click="downloadDoc(row)">下载</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="mobile-item-list">
            <div v-for="d in publishedDocs" :key="d.id" class="mobile-item-row">
              <span class="mobile-item-title">{{ d.title }}</span>
              <el-button size="small" text type="primary" @click="downloadDoc(d)">下载</el-button>
            </div>
          </div>
        </div>
      </section>

      <section v-if="portalContent.billing || portalContent.invoice" class="app-card">
        <div class="app-card-head">
          <h2>账单与收款</h2>
          <span v-if="billingInvoices.length" class="app-card-head-meta">{{ billingInvoices.length }} 张费用通知单</span>
        </div>
        <div class="app-card-body">
          <div v-if="billingSummary" class="billing-summary">
            <div class="app-stat">
              <span class="app-stat-label">账单总额</span>
              <div class="app-stat-value">{{ money(billingSummary.total_invoiced) }}</div>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">已收金额</span>
              <div class="app-stat-value billing-paid">{{ money(billingSummary.total_paid) }}</div>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">待收金额</span>
              <div class="app-stat-value billing-outstanding">{{ money(billingSummary.total_outstanding) }}</div>
            </div>
          </div>
          <div v-for="invoice in billingInvoices" :key="invoice.id || invoice.invoice_number" class="portal-invoice">
            <div class="portal-invoice-heading">
              <div>
                <strong>{{ invoice.invoice_number }}</strong>
                <span class="invoice-meta">{{ invoice.issue_date || '开具日期未记录' }}</span>
              </div>
              <div>
                <span class="app-tag" :class="invoiceStatusTagClass(invoice.status)">{{ invoiceStatusLabel(invoice.status) }}</span>
                <span v-if="invoice.due_date" class="invoice-meta">应付款日 {{ invoice.due_date }}</span>
              </div>
            </div>
            <dl class="portal-invoice-facts">
              <div><dt>账期</dt><dd>{{ invoice.period_start || '-' }} ~ {{ invoice.period_end || '-' }}</dd></div>
              <div><dt>含税金额</dt><dd>{{ money(invoice.total_amount, invoice.currency) }}</dd></div>
              <div><dt>已收</dt><dd>{{ money(invoice.paid_amount, invoice.currency) }}</dd></div>
              <div><dt>待收</dt><dd>{{ money(invoice.outstanding_amount, invoice.currency) }}</dd></div>
            </dl>
            <table v-if="invoice.items?.length" class="portal-invoice-items">
              <thead>
                <tr><th>项目</th><th>数量</th><th>单价</th><th>金额</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in invoice.items" :key="`${invoice.id}-${item.title}-${item.amount}`">
                  <td>{{ item.title }}</td>
                  <td>{{ item.quantity }}</td>
                  <td>{{ money(item.unit_price, invoice.currency) }}</td>
                  <td>{{ money(item.amount, invoice.currency) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="invoice.payments?.length" class="portal-payment-list">
              <span class="billing-subheading">收款记录</span>
              <div v-for="payment in invoice.payments" :key="`${invoice.id}-${payment.recorded_at}-${payment.amount}`">
                <span>{{ payment.recorded_at || '时间未记录' }}</span>
                <strong>{{ money(payment.amount, payment.currency) }}</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="app-card">
        <div class="app-card-head">
          <h2>服务反馈</h2>
        </div>
        <div class="app-card-body">
          <div v-if="feedbackDone" class="feedback-done">
            <span class="app-tag" :class="feedbackValue === 1 ? 'is-ok' : 'is-pending'">
              {{ feedbackValue === 1 ? '已收到反馈：有帮助' : '已收到反馈：待改进' }}
            </span>
            <span class="muted">感谢您的评价。</span>
          </div>
          <div v-else class="feedback-row">
            <span class="feedback-label">本次服务对您有帮助吗？</span>
            <el-button size="small" type="success" plain :loading="feedbackSubmitting === 1" @click="submitFeedback(1)">有帮助</el-button>
            <el-button size="small" type="warning" plain :loading="feedbackSubmitting === -1" @click="submitFeedback(-1)">待改进</el-button>
            <div v-if="feedbackNoteOpen" class="feedback-panel">
              <el-input v-model="feedbackNote" type="textarea" :rows="2" maxlength="500" show-word-limit placeholder="补充说明（可选，最多500字）" />
              <div class="feedback-actions">
                <el-button size="small" type="primary" @click="confirmFeedback">提交反馈</el-button>
                <el-button size="small" @click="feedbackNoteOpen = false; feedbackNote = ''">取消</el-button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section v-if="portalContent.sign_requests?.length" class="app-card">
        <div class="app-card-head">
          <h2>签署请求</h2>
          <span class="app-card-head-meta">共 {{ portalContent.sign_requests.length }} 份</span>
        </div>
        <div class="app-card-body">
          <el-table class="desktop-table" :data="portalContent.sign_requests" stripe size="small">
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="title" label="文档" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="100">
              <template #default="{ row }">
                <span class="app-tag" :class="row.status === 'pending' ? 'is-pending' : 'is-ok'">{{ row.status === 'pending' ? '待签署' : '已签署' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending'" size="small" text type="primary" @click="openSignLink(row)">前往签署</el-button>
                <span v-else class="muted">已完成</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="mobile-item-list">
            <div v-for="row in portalContent.sign_requests" :key="row.id" class="mobile-item-row">
              <span class="mobile-item-title">{{ row.title }}</span>
              <span class="app-tag" :class="row.status === 'pending' ? 'is-pending' : 'is-ok'">{{ row.status === 'pending' ? '待签署' : '已签署' }}</span>
              <el-button v-if="row.status === 'pending'" size="small" text type="primary" @click="openSignLink(row)">前往签署</el-button>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../api'

const route = useRoute()
const token = route.params.token

const step = ref('otp')
const errorState = ref('')
// OTP 邮件发送失败（503/限流）不进终态：留在验证页允许重试（ux-audit P1-4）
const otpError = ref('')
const organizationName = ref('')
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

const feedbackValue = ref(null)
const feedbackDone = ref(false)
const feedbackSubmitting = ref(0)
const feedbackNoteOpen = ref(false)
const feedbackNote = ref('')

const submitFeedback = (score) => {
  if (score === 1) {
    doSubmitFeedback(1)
  } else {
    feedbackNoteOpen.value = true
  }
}

const confirmFeedback = async () => {
  await doSubmitFeedback(-1)
}

const doSubmitFeedback = async (score) => {
  feedbackSubmitting.value = score
  try {
    const note = feedbackNote.value.trim()
    await api.portalSubmitFeedback(token, { score, note: note || null }, portalSession.value)
    feedbackValue.value = score
    feedbackDone.value = true
    feedbackNoteOpen.value = false
    ElMessage.success('反馈已提交')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '反馈提交失败')
  }
  feedbackSubmitting.value = 0
}

const initOtp = async () => {
  try {
    const { data } = await api.portalSendOtp(token)
    maskedEmail.value = data.email_masked || ''
    organizationName.value = data.organization_name || organizationName.value
    otpError.value = ''
    startCooldown()
  } catch (e) {
    const detail = e.response?.data?.detail
    const status = e.response?.status
    if (status === 404) errorState.value = '链接不存在'
    else if (status === 410) errorState.value = '链接已过期'
    else if (status === 403) errorState.value = '链接已被撤销'
    else if (status === 400) errorState.value = detail || '链接配置有误'
    // 503（邮件服务暂不可用）/429（发送频率限制）/网络错误：可重试，不进终态死胡同
    else if (status === 503 || status === 429 || !e.response) otpError.value = detail || '验证码邮件暂时无法发送，请稍后重试'
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
    maskedEmail.value = data.email_masked || maskedEmail.value
    organizationName.value = data.organization_name || organizationName.value
    otpError.value = ''
    startCooldown()
    ElMessage.success('验证码已重新发送')
  } catch (e) {
    const status = e.response?.status
    const detail = e.response?.data?.detail
    ElMessage.error(detail || '发送失败')
    if (status === 503 || status === 429 || !e.response) {
      otpError.value = detail || '验证码邮件暂时无法发送，请稍后重试'
    }
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
// 方向 B：状态一律用 .app-tag 胶囊，不再走 el-tag 的 type 映射
const invoiceStatusTagClass = (s) => ({ paid: 'is-ok', sent: 'is-pending', overdue: 'is-danger', voided: 'is-info' }[s] || '')
const money = (value, currency = 'CNY') => `${currency === 'CNY' ? '¥' : currency + ' '}${Number(value || 0).toFixed(2)}`
const billingSummary = computed(() => portalContent.value.billing || (portalContent.value.invoice ? {
  total_invoiced: portalContent.value.invoice.total_amount,
  total_paid: portalContent.value.invoice.paid_amount,
  total_outstanding: portalContent.value.invoice.outstanding_amount,
} : null))
const billingInvoices = computed(() => portalContent.value.billing?.invoices || (portalContent.value.invoice ? [portalContent.value.invoice] : []))
const CASE_STATUS_LABELS = { in_progress: '进行中', closed: '已结案', archived: '已归档' }
const caseStatusLabel = (status) => CASE_STATUS_LABELS[status] || status || '进行中'
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
/* 公开页自成一屏：无外壳，因此自带页边距与块间距（方向 B 的卡片流） */
.legal-portal {
  display: grid;
  gap: var(--space-5);
  align-content: start;
  max-width: 880px;
  min-height: 60vh;
  margin: 0 auto;
  padding: var(--space-6) var(--space-5) var(--space-10);
}

/* ─── 品牌头 ─── */
.portal-masthead {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.portal-masthead-brand {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.portal-logo-mark {
  width: 34px;
  height: 34px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  /* 全站唯一的品牌渐变：34px 身份标记，不铺面、不加光晕 */
  background: var(--gradient-brand-mark);
  color: var(--color-text-inverse);
  font-size: var(--text-lg);
  font-weight: 700;
  letter-spacing: 0.02em;
}

.portal-masthead-text {
  display: grid;
  gap: 1px;
  min-width: 0;
}

.portal-masthead-name {
  color: var(--color-text);
  font-size: var(--text-lg);
  font-weight: 700;
  letter-spacing: 0.01em;
}

.portal-masthead-sub {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.portal-masthead-org {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
  margin-left: auto;
}

.portal-org-logo {
  max-width: 132px;
  max-height: 34px;
  object-fit: contain;
  border-radius: var(--radius-xs);
}

.portal-org-name {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 600;
  overflow-wrap: anywhere;
}

/* ─── 链接失效态 ─── */
.portal-state-card {
  align-self: start;
}

.portal-error {
  display: grid;
  min-height: 240px;
  align-content: center;
  justify-items: center;
  padding: var(--space-8) var(--space-5);
}

.portal-error-hint {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  text-align: center;
}

/* ─── 页头与验证码卡 ─── */
.portal-page-head {
  min-width: 0;
}

.otp-sent-alert,
.otp-error-alert {
  margin-bottom: var(--space-4);
}

.otp-error-hint {
  margin: 6px 0 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}

.otp-input {
  /* 375px 专项 M-10：窄屏随容器满宽，桌面保持 240px */
  width: min(240px, 100%);
}

.otp-actions {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
}

/* ─── 进展时间线（骨架用 .app-timeline*，这里只补文字层级） ─── */
.timeline-item-body {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.progress-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.progress-title {
  color: var(--color-text);
  font-size: var(--text-base);
  font-weight: 600;
}

.progress-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.progress-body {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.progress-next {
  display: grid;
  gap: 2px;
  margin-top: var(--space-2);
  padding: 10px var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}

.progress-next strong {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
}

.progress-next p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.7;
}

.muted {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

/* ─── 账单与收款 ─── */
.billing-summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

/* 三列并排的金额串较长，按共享 .app-stat 的 28px 会溢出单元格，这里降到 22px */
.billing-summary .app-stat-value {
  font-size: var(--text-2xl);
}

.billing-summary .billing-paid {
  color: var(--color-success);
}

.billing-summary .billing-outstanding {
  color: var(--color-warning);
}

.portal-invoice {
  padding: var(--space-4) 0;
  border-bottom: 1px solid var(--color-border-light);
}

.portal-invoice:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.portal-invoice-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.portal-invoice-heading > div {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  min-width: 0;
}

.portal-invoice-heading strong {
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
}

.invoice-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.portal-invoice-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  margin: var(--space-4) 0 0;
}

.portal-invoice-facts div {
  display: grid;
  gap: var(--space-1);
  min-width: 0;
}

.portal-invoice-facts dt {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.portal-invoice-facts dd {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

.portal-invoice-items {
  width: 100%;
  margin-top: var(--space-4);
  border-collapse: collapse;
  table-layout: fixed;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
}

.portal-invoice-items th,
.portal-invoice-items td {
  padding: 7px var(--space-2);
  border-bottom: 1px solid var(--color-border-light);
  text-align: left;
}

.portal-invoice-items th {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 500;
}

.portal-invoice-items th:first-child,
.portal-invoice-items td:first-child {
  overflow-wrap: anywhere;
}

.portal-invoice-items th:nth-child(n + 2),
.portal-invoice-items td:nth-child(n + 2) {
  width: 18%;
  text-align: right;
  white-space: nowrap;
}

.portal-payment-list {
  display: grid;
  gap: 6px;
  margin-top: var(--space-4);
}

.billing-subheading {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 600;
}

.portal-payment-list > div {
  display: flex;
  justify-content: space-between;
  gap: var(--space-3);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}

/* ─── 服务反馈 ─── */
.feedback-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.feedback-label {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.feedback-done {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.feedback-panel {
  display: grid;
  gap: var(--space-2);
  width: 100%;
  margin-top: var(--space-1);
  padding: var(--space-3);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}

.feedback-actions {
  display: flex;
  gap: var(--space-2);
}

/* 375px 专项 M-10：文档/签署双布局——桌面表格、窄屏卡片行 */
.mobile-item-list {
  display: none;
}

.mobile-item-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 11px 0;
  border-bottom: 1px solid var(--color-border-light);
}

.mobile-item-row:last-child {
  border-bottom: 0;
}

.mobile-item-title {
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  font-size: var(--text-sm);
  overflow-wrap: anywhere;
}

@media (max-width: 640px) {
  .legal-portal {
    max-width: 100%;
    gap: var(--space-4);
    padding: var(--space-4) var(--space-3) var(--space-8);
  }

  /* 触控目标：small 按钮仅 24px 高，移动端抬到 40px（ux-audit M-10） */
  .legal-portal .el-button {
    min-height: 40px;
  }

  .portal-masthead-org {
    margin-left: 0;
  }

  .desktop-table {
    display: none;
  }

  .mobile-item-list {
    display: grid;
  }

  .billing-summary {
    grid-template-columns: 1fr;
  }

  .portal-invoice-facts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .portal-invoice-heading {
    flex-direction: column;
    gap: var(--space-2);
  }

  /* 375px 专项：行项目表数字列过窄，金额被 overflow-wrap 从中间折断——缩小字号 + 收紧内边距 + 窄化数量列 */
  .portal-invoice-items {
    font-size: var(--text-xs);
  }

  .portal-invoice-items th,
  .portal-invoice-items td {
    padding: 6px 4px;
  }

  .portal-invoice-items th:nth-child(2),
  .portal-invoice-items td:nth-child(2) {
    width: 12%;
  }

  .portal-invoice-items th:nth-child(n + 3),
  .portal-invoice-items td:nth-child(n + 3) {
    width: 23%;
  }

  .progress-header {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-1);
  }
}

.portal-case-body { display: grid; gap: 4px; }
.portal-case-title { margin: 0; color: var(--color-text); font-size: var(--text-md); font-weight: 600; }
</style>
