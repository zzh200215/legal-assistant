<template>
  <div class="login-page">
    <div class="login-brand">
      <div class="brand-content">
        <div class="brand-kicker">法律智能工作台</div>
        <div class="brand-title-row">
          <div class="brand-mark">律</div>
          <div>
            <h1 class="brand-title">律智检</h1>
            <p class="brand-subtitle">法律检索、合同审查、文书草稿与律师审核</p>
          </div>
        </div>
        <div class="brand-metrics">
          <div><strong>RAG</strong><span>法规检索</span></div>
          <div><strong>Agent</strong><span>合同审查</span></div>
          <div><strong>Ops</strong><span>律师审核</span></div>
        </div>
        <div class="brand-feature-list">
          <span>法律咨询与事实补充</span>
          <span>合同条款风险识别</span>
          <span>法律文书草稿生成</span>
          <span>律师审核与版本留痕</span>
        </div>
      </div>
      <div class="brand-footer">律智检 Law Intelligence</div>
    </div>

    <div class="login-form-panel">
      <div class="form-container">
        <div class="form-header">
          <h2>账号登录</h2>
          <p>登录后进入法律工作台</p>
          <el-tabs v-model="tab" class="login-tabs">
            <el-tab-pane label="登录" name="login" />
            <el-tab-pane label="注册" name="register" />
          </el-tabs>
        </div>

        <el-form v-show="tab === 'login'" @submit.prevent="handleLogin" class="login-form">
          <el-form-item>
            <el-input v-model="loginForm.username" placeholder="用户名" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="loginForm.password" type="password" placeholder="密码" show-password :prefix-icon="LockIcon" size="large" />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="handleLogin" class="submit-btn" size="large">
            {{ loading ? '登录中...' : '登录' }}
          </el-button>
        </el-form>

        <el-form v-show="tab === 'register'" @submit.prevent="handleRegister" class="login-form">
          <el-form-item>
            <el-input v-model="regForm.username" placeholder="用户名" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.email" placeholder="邮箱" :prefix-icon="MessageIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.password" type="password" placeholder="密码" show-password :prefix-icon="LockIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.full_name" placeholder="姓名（可选）" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="handleRegister" class="submit-btn" size="large">
            {{ loading ? '注册中...' : '注册' }}
          </el-button>
        </el-form>

        <p class="form-hint">首次使用可注册账号，注册后自动登录。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/tabs/style/css'
import api from '../api'
import { ElMessage } from 'element-plus/es/components/message/index'

const router = useRouter()
const tab = ref('login')
const loading = ref(false)

const loginForm = ref({ username: '', password: '' })
const regForm = ref({ username: '', email: '', password: '', full_name: '' })

const UserIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('path', { d: 'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2' }),
  h('circle', { cx: 12, cy: 7, r: 4 }),
])
const LockIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('rect', { x: 3, y: 11, width: 18, height: 11, rx: 2 }),
  h('path', { d: 'M7 11V7a5 5 0 0110 0v4' }),
])
const MessageIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('path', { d: 'M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z' }),
])

const handleLogin = async () => {
  if (!loginForm.value.username || !loginForm.value.password) {
    return ElMessage.warning('请输入用户名和密码')
  }
  loading.value = true
  try {
    const { data } = await api.login(loginForm.value)
    localStorage.setItem('token', data.access_token)
    try {
      const me = await api.getMe()
      localStorage.setItem('user_role', me.data.role || 'user')
    } catch {
      localStorage.removeItem('token')
      ElMessage.error('登录失败，无法获取用户信息')
      loading.value = false
      return
    }
    ElMessage.success('登录成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  }
  loading.value = false
}

const handleRegister = async () => {
  if (!regForm.value.username || !regForm.value.email || !regForm.value.password) {
    return ElMessage.warning('请填写必填项')
  }
  loading.value = true
  try {
    const { data } = await api.register(regForm.value)
    localStorage.setItem('token', data.access_token)
    try {
      const me = await api.getMe()
      localStorage.setItem('user_role', me.data.role || 'user')
    } catch {
      localStorage.removeItem('token')
      ElMessage.error('注册失败，无法获取用户信息')
      loading.value = false
      return
    }
    ElMessage.success('注册成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  }
  loading.value = false
}
</script>

<style scoped>
.login-page {
  display: flex;
  min-height: 100vh;
  background:
    radial-gradient(circle at 16% 12%, rgba(79, 106, 245, 0.16), transparent 34%),
    linear-gradient(135deg, #FFFFFF 0%, #F0F2FF 52%, #EAF8FF 100%);
}

.login-brand {
  flex: 1.1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  position: relative;
  background: transparent;
  color: var(--color-text);
  overflow: hidden;
  padding: 56px 72px;
}
.login-brand::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse at 20% 44%, rgba(79, 106, 245, 0.12) 0%, transparent 58%),
    radial-gradient(ellipse at 80% 20%, rgba(34, 197, 94, 0.10) 0%, transparent 48%);
  pointer-events: none;
}

.brand-content {
  position: relative;
  z-index: 1;
  max-width: 560px;
}

.brand-kicker {
  font-size: var(--text-sm);
  font-weight: 600;
  margin-bottom: 18px;
  color: var(--color-primary);
  letter-spacing: 0;
  text-transform: uppercase;
}

.brand-title-row {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

.brand-mark {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  background: var(--gradient-brand);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  font-size: 18px;
  flex-shrink: 0;
  box-shadow: 0 14px 32px rgba(79, 106, 245, 0.28);
}

.brand-title {
  font-size: 34px;
  font-weight: 800;
  margin: 0 0 8px;
  letter-spacing: 0;
  color: var(--color-text);
}

.brand-subtitle {
  font-size: var(--text-base);
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.6;
}

.brand-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 40px;
}

.brand-metrics div {
  padding: 16px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(10px);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: 6px;
  transition: all var(--transition-fast);
}
.brand-metrics div:hover {
  background: #ffffff;
  border-color: var(--color-border-hover);
  box-shadow: var(--shadow-card-hover);
  transform: translateY(-2px);
}

.brand-metrics strong {
  color: var(--color-text);
  font-size: var(--text-2xl);
}

.brand-metrics span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.brand-feature-list {
  display: grid;
  gap: 10px;
  margin-top: 28px;
  font-size: var(--text-sm);
}

.brand-feature-list span {
  padding-left: 14px;
  border-left: 2px solid var(--color-primary);
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}
.brand-feature-list span:hover {
  border-left-color: var(--color-accent);
  padding-left: 18px;
  color: var(--color-primary);
}

.brand-footer {
  position: absolute;
  left: 72px;
  bottom: 32px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  letter-spacing: 0;
}

.login-form-panel {
  width: 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.88);
  border-left: 1px solid rgba(228, 232, 248, 0.82);
  box-shadow: -20px 0 50px rgba(79, 106, 245, 0.08);
  backdrop-filter: blur(18px);
  padding: 48px;
}

.form-container {
  width: 100%;
  max-width: 360px;
}

.form-header {
  margin-bottom: 24px;
}

.form-header h2 {
  margin: 0 0 6px;
  font-size: var(--text-3xl);
  font-weight: 700;
  color: var(--color-text);
  letter-spacing: -0.02em;
}

.form-header p {
  margin: 0 0 18px;
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

.login-tabs {
  --el-tabs-header-height: 40px;
}

.login-tabs :deep(.el-tabs__item) {
  font-size: var(--text-base);
  font-weight: 500;
  height: 40px;
  line-height: 40px;
  color: var(--color-text-muted);
}

.login-tabs :deep(.el-tabs__item.is-active) {
  color: var(--color-primary);
}

.login-tabs :deep(.el-tabs__active-bar) {
  height: 2.5px;
  border-radius: 2px;
}

.login-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.login-form {
  margin-top: 8px;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-form :deep(.el-input__wrapper) {
  padding: 4px 14px;
  height: 44px;
}

.login-form :deep(.el-input__prefix) {
  margin-right: 10px;
  color: var(--color-text-muted);
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
  font-weight: 800;
  letter-spacing: 0;
  height: 44px !important;
}

.form-hint {
  margin-top: 24px;
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

@media (max-width: 900px) {
  .login-page {
    flex-direction: column;
  }
  .login-brand {
    padding: 48px 24px 80px;
    min-height: 320px;
  }
  .login-form-panel {
    width: 100%;
    padding: 40px 24px;
  }
  .brand-title {
    font-size: var(--text-3xl);
  }
  .brand-metrics {
    grid-template-columns: 1fr 1fr;
  }
  .brand-footer {
    left: 24px;
  }
}
</style>
