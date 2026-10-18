<template>
  <main class="developer-page">
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">开发者与安全管理</h1>
        <p class="app-page-sub">管理开发者应用密钥，查看后台任务与 Webhook 的运营状态。</p>
      </div>
    </header>
    <el-alert v-if="error" type="error" :title="error" :closable="false" />
    <section class="app-card">
      <div class="app-card-head">
        <h2>运营概览</h2>
      </div>
      <div class="app-card-body">
        <div class="developer-metrics">
          <div class="app-stat"><span class="k">排队任务</span><span class="v">{{ summary.queued_jobs || 0 }}</span></div>
          <div class="app-stat"><span class="k">失败任务</span><span class="v">{{ summary.failed_jobs || 0 }}</span></div>
          <div class="app-stat"><span class="k">待投递 Webhook</span><span class="v">{{ summary.pending_webhooks || 0 }}</span></div>
          <div class="app-stat"><span class="k">失败 Webhook</span><span class="v">{{ summary.failed_webhooks || 0 }}</span></div>
          <div class="app-stat"><span class="k">API 调用</span><span class="v">{{ summary.api_calls || 0 }}</span></div>
          <div class="app-stat"><span class="k">回调验签失败</span><span class="v">{{ summary.callback_verification_failures || 0 }}</span></div>
        </div>
      </div>
    </section>
    <section class="app-card">
      <div class="app-card-head">
        <h2>开发者应用</h2>
      </div>
      <div class="app-card-body">
        <el-form inline @submit.prevent="createApp"><el-form-item><el-input v-model="name" placeholder="应用名称" /></el-form-item><el-button type="primary" @click="createApp">创建并显示一次性密钥</el-button></el-form>
        <el-alert v-if="oneTimeKey" type="warning" :title="`请立即保存密钥：${oneTimeKey}`" :closable="false" />
        <el-table :data="apps"><el-table-column prop="name" label="名称" /><el-table-column prop="status" label="状态" /><el-table-column label="操作"><template #default="{ row }"><el-button @click="rotate(row)">轮换密钥</el-button></template></el-table-column></el-table>
      </div>
    </section>
  </main>
</template>
<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../api'
// org 不能从 localStorage 取：全项目从未写入 organization_id，恒为 1，非 org-1 的 admin 必然 403。
// 从 /auth/me 解析真实组织，localStorage 仅作兜底。
const orgId = ref(null)
const apps = ref([]), summary = ref({}), name = ref(''), oneTimeKey = ref(''), error = ref('')
async function load() {
  try {
    const { data } = await api.getMe()
    orgId.value = Number(data?.organization_id) || Number(localStorage.getItem('organization_id')) || null
  } catch {
    orgId.value = Number(localStorage.getItem('organization_id')) || null
  }
  if (!orgId.value) { error.value = '无法解析组织信息，请重新登录'; return }
  try {
    const [appsRes, summaryRes] = await Promise.all([
      api.listDeveloperApps(orgId.value),
      api.getOperationsSummary(orgId.value),
    ])
    apps.value = appsRes.data
    summary.value = summaryRes.data
  } catch (e) { error.value = e.response?.data?.detail || '无管理员权限或数据加载失败' }
}
async function createApp() {
  if (!orgId.value) return ElMessage.error('组织信息缺失，请刷新重试')
  if (!name.value.trim()) return
  try { const { data } = await api.createDeveloperApp(orgId.value, { name: name.value }); oneTimeKey.value = data.api_key; name.value = ''; await load() } catch (e) { ElMessage.error(e.response?.data?.detail || '创建失败') }
}
async function rotate(row) {
  if (!orgId.value) return ElMessage.error('组织信息缺失，请刷新重试')
  try { const { data } = await api.rotateDeveloperKey(orgId.value, row.id); oneTimeKey.value = data.new_api_key } catch (e) { ElMessage.error(e.response?.data?.detail || '轮换失败') }
}
onMounted(load)
</script>
<style scoped>
/* 页头/卡片走 design-tokens.css 的 .app-page-head / .app-card，这里只排布指标格 */
.developer-page { display: grid; gap: var(--space-5); max-width: 1100px; padding-bottom: var(--space-8); }
.developer-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: var(--space-3); }
</style>
