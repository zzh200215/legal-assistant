<template>
  <main class="onboarding">
    <div class="onboarding-head">
      <h2>法律工作台引导</h2>
      <p>按你的角色完成前几步，10 分钟出第一个成果，再进入工作台继续。</p>
    </div>
    <el-card class="onboarding-card">
      <div class="role-row">
        <span class="role-label">我的角色：</span>
        <el-radio-group v-model="role">
          <el-radio-button :value="'solo_lawyer'">独立律师</el-radio-button>
          <el-radio-button :value="'firm_admin'">律所管理员</el-radio-button>
          <el-radio-button :value="'enterprise_legal'">企业法务</el-radio-button>
        </el-radio-group>
      </div>
      <el-steps direction="vertical" :active="completed.length" class="steps">
        <el-step v-for="item in steps" :key="item" :title="item" />
      </el-steps>
      <div class="onboarding-actions">
        <el-button @click="complete">保存进度</el-button>
        <el-button type="primary" @click="enterWorkspace">进入工作台 →</el-button>
      </div>
    </el-card>
  </main>
</template>
<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import http from '../api/http'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElRadioGroup, ElRadioButton } from 'element-plus/es/components/radio/index'
import { ElSteps, ElStep } from 'element-plus/es/components/steps/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/steps/style/css'
import 'element-plus/es/components/step/style/css'

const router = useRouter()
const role = ref('solo_lawyer')
const completed = ref([])
const steps = computed(() => ({
  solo_lawyer: ['创建案件', '完成合同审查', '发布客户门户'],
  firm_admin: ['配置成员', '创建审查策略', '查看运营数据'],
  enterprise_legal: ['创建合同台账', '设置关键日期', '发起审批'],
}[role.value]))

onMounted(async () => {
  try {
    const { data } = await http.get('/developer/onboarding')
    if (data) {
      role.value = data.user_role || role.value
      completed.value = JSON.parse(data.completed_steps_json || '[]')
    }
  } catch {}
})

async function complete() {
  completed.value = steps.value
  await http.put('/developer/onboarding', { user_role: role.value, completed_steps_json: JSON.stringify(completed.value) })
  ElMessage.success('引导进度已保存')
}

function enterWorkspace() {
  router.push('/legal-workspace')
}
</script>
<style scoped>
.onboarding { max-width: 760px; margin: 32px auto; padding: 0 20px; }
.onboarding-head p { color: var(--color-text-muted); margin: 8px 0 20px; }
.onboarding-card { padding: 8px; }
.role-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.role-label { font-weight: 600; }
.steps { margin: 24px 0; }
.onboarding-actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 16px; }
</style>
