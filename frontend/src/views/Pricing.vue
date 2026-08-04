<template>
  <div class="pricing-page">
    <div class="pricing-header">
      <h2 class="pricing-title">订阅方案</h2>
      <p class="pricing-sub">按团队配额计费，AI 咨询/合同审查/文书草稿共享额度</p>
      <el-tag v-if="myPlan" size="small" type="success" effect="plain">当前方案：{{ myPlan.name }}（{{ myPlan.status }}）</el-tag>
    </div>

    <div class="plan-grid" v-if="plans.length">
      <div v-for="plan in plans" :key="plan.id" class="plan-card" :class="{ current: isCurrent(plan) }">
        <div class="plan-card-head">
          <span class="plan-name">{{ plan.name }}</span>
          <el-tag v-if="isCurrent(plan)" size="small" type="success">当前</el-tag>
        </div>
        <div class="plan-price">
          <span class="price-num">¥{{ plan.price_monthly }}</span>
          <span class="price-unit">/月</span>
        </div>
        <p class="plan-desc">{{ plan.description }}</p>
        <ul class="plan-quotas">
          <li>咨询 {{ plan.quota_consultation }} 次/月</li>
          <li>合同审查 {{ plan.quota_review }} 次/月</li>
          <li>文书草稿 {{ plan.quota_draft }} 次/月</li>
        </ul>
        <div class="plan-actions">
          <el-button v-if="isCurrent(plan)" size="small" disabled>当前方案</el-button>
          <el-button v-else size="small" type="primary" :loading="buyingTier === plan.tier" @click="buy(plan)">
            {{ plan.price_monthly > 0 ? '升级' : '使用免费版' }}
          </el-button>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无方案" />

    <el-alert
      v-if="checkoutMessage"
      :title="checkoutMessage"
      type="warning"
      show-icon
      :closable="false"
      style="margin-top: 20px"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/message/style/css'
import subscriptionApi from '../api/subscription'

const plans = ref([])
const myPlan = ref(null)
const buyingTier = ref('')
const checkoutMessage = ref('')

const isCurrent = (plan) => myPlan.value?.tier === plan.tier

const load = async () => {
  try {
    const data = await subscriptionApi.listPlans()
    plans.value = data || []
  } catch { plans.value = [] }
  try {
    const me = await subscriptionApi.mySubscription()
    myPlan.value = me?.plan || null
  } catch { myPlan.value = null }
}

const buy = async (plan) => {
  buyingTier.value = plan.tier
  checkoutMessage.value = ''
  try {
    const res = await subscriptionApi.checkout(plan.tier)
    if (res?.configured && res.checkout_url) {
      window.location.href = res.checkout_url
    } else {
      checkoutMessage.value = res?.message || '支付网关尚未配置，请联系管理员开通后购买'
    }
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    buyingTier.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.pricing-page {
  padding: 24px;
  max-width: 960px;
  margin: 0 auto;
}
.pricing-header {
  margin-bottom: 24px;
}
.pricing-title {
  margin: 0 0 4px;
  font-size: 20px;
}
.pricing-sub {
  margin: 0 0 8px;
  color: #909399;
  font-size: 13px;
}
.plan-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}
.plan-card {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 20px;
  display: flex;
  flex-direction: column;
}
.plan-card.current {
  border-color: #67c23a;
  box-shadow: 0 0 0 1px #67c23a;
}
.plan-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.plan-name {
  font-weight: 600;
  font-size: 15px;
}
.plan-price {
  margin: 12px 0 4px;
}
.price-num {
  font-size: 26px;
  font-weight: 700;
}
.price-unit {
  color: #909399;
  margin-left: 2px;
}
.plan-desc {
  color: #606266;
  font-size: 12px;
  min-height: 32px;
}
.plan-quotas {
  padding-left: 18px;
  color: #606266;
  font-size: 13px;
  line-height: 1.9;
  flex: 1;
}
.plan-actions {
  margin-top: 12px;
}
</style>
