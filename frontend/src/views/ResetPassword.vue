<template>
  <div class="reset-page">
    <div class="reset-card">
      <template v-if="resetState === 'form'">
        <header class="reset-header">
          <p class="reset-kicker">律智检</p>
          <h1>设置新密码</h1>
          <p class="reset-subtitle">请输入新密码完成重置，重置成功后将自动登录。</p>
        </header>
        <el-form @submit.prevent="handleReset" class="reset-form">
          <el-form-item>
            <el-input
              v-model="form.new_password"
              type="password"
              placeholder="新密码（至少 8 位）"
              show-password
              :prefix-icon="LockIcon"
              size="large"
            />
          </el-form-item>
          <el-form-item>
            <el-input
              v-model="form.confirm_password"
              type="password"
              placeholder="再次输入新密码"
              show-password
              :prefix-icon="LockIcon"
              size="large"
            />
          </el-form-item>
          <el-button type="primary" :loading="loading" class="submit-btn" size="large" @click="handleReset">
            {{ loading ? '提交中...' : '重置密码' }}
          </el-button>
        </el-form>
        <div class="reset-footer">
          <button type="button" class="text-link" @click="router.push('/login')">返回登录</button>
        </div>
      </template>

      <template v-else-if="resetState === 'invalid'">
        <header class="reset-header">
          <h1>链接无效或已过期</h1>
          <p class="reset-subtitle">重置链接有效期为 30 分钟，且只能使用一次。</p>
        </header>
        <el-button type="primary" class="submit-btn" size="large" @click="goForgot">重新发送重置邮件</el-button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import { ElMessage } from 'element-plus/es/components/message/index'
import api from '../api'
import { setAccessToken, setRefreshToken } from '../api/http'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const loading = ref(false)
const form = ref({ new_password: '', confirm_password: '' })
// 邮件链接格式：{前端地址}/reset-password?token=xxx
const token = computed(() => String(route.query.token || '').trim())
const resetState = ref(token.value ? 'form' : 'invalid')

const LockIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('rect', { x: 3, y: 11, width: 18, height: 11, rx: 2 }),
  h('path', { d: 'M7 11V7a5 5 0 0110 0v4' }),
])

const handleReset = async () => {
  const pwd = form.value.new_password
  if (!pwd || pwd.length < 8) return ElMessage.warning('新密码至少 8 位')
  if (pwd !== form.value.confirm_password) return ElMessage.warning('两次输入的密码不一致')
  loading.value = true
  try {
    const { data } = await api.resetPassword({ token: token.value, new_password: pwd })
    setAccessToken(data.access_token)
    setRefreshToken(data.refresh_token || null)
    try {
      const me = await api.getMe()
      authStore.setUser(me.data)
    } catch {
      // 会话已签发，用户信息可稍后在 /auth/me 拉取
    }
    ElMessage.success('密码已重置，正在进入工作台')
    router.push('/')
  } catch (e) {
    const detail = e.response?.data?.detail
    // token 无效/过期是终态：留在表单只会反复失败，切换到引导重新发送
    if (e.response?.data?.error?.code === 'INVALID_RESET_TOKEN' || /无效|过期/.test(String(detail || ''))) {
      resetState.value = 'invalid'
    } else {
      ElMessage.error(detail || '重置失败，请稍后再试')
    }
  } finally {
    loading.value = false
  }
}

const goForgot = () => router.push({ path: '/login', query: { tab: 'forgot' } })
</script>

<style scoped>
.reset-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: var(--color-bg-canvas, #F5F7FA);
}

.reset-card {
  width: min(420px, 100%);
  padding: 40px 36px;
  background: #fff;
  border: 1px solid var(--color-border-light, #E4E8EE);
  border-radius: 10px;
  box-shadow: 0 8px 30px rgba(15, 30, 60, 0.06);
}

.reset-header {
  margin-bottom: 22px;
}

.reset-kicker {
  margin: 0 0 6px;
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 600;
}

.reset-header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 620;
  color: var(--color-text);
}

.reset-subtitle {
  margin: 8px 0 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
  font-weight: 500;
  height: 44px !important;
}

.reset-footer {
  display: flex;
  justify-content: center;
  margin-top: 18px;
}

.text-link {
  border: 0;
  background: transparent;
  padding: 0;
  color: var(--color-primary);
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}

.text-link:hover {
  color: var(--color-primary-hover);
  text-decoration: underline;
}
</style>
