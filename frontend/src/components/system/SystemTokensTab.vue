<template>
  <div class="app-section-intro tab-intro">
    <strong>成本与使用分析</strong>
    <span>跟踪调用量、限额、成本和问答回放，判断模型资源是否被有效使用。</span>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-body">
      <div class="app-filter-bar">
        <span class="app-filter-label">统计周期：</span>
        <el-radio-group v-model="tokenDays" @change="resetTokenPagination">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <div class="app-filter-bar-actions">
          <el-button @click="fetchTokenData">刷新</el-button>
        </div>
      </div>
    </div>
  </section>

  <div class="app-metric-grid-4 token-block">
    <div class="app-stat">
      <span class="app-stat-label">总调用次数 · 近 {{ tokenDays }} 天</span>
      <span class="app-stat-value">{{ myStats.total_calls || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">总 Token 数 · 近 {{ tokenDays }} 天</span>
      <span class="app-stat-value">{{ myStats.total_tokens || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">输入 Token · 近 {{ tokenDays }} 天</span>
      <span class="app-stat-value">{{ myStats.total_prompt_tokens || 0 }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">输出 Token · 近 {{ tokenDays }} 天</span>
      <span class="app-stat-value">{{ myStats.total_completion_tokens || 0 }}</span>
    </div>
  </div>

  <div class="app-metric-grid-4 token-block">
    <div class="app-stat">
      <span class="app-stat-label">今日已用请求</span>
      <span class="app-stat-value">{{ myGovernance.today.used_requests || 0 }}</span>
      <span class="app-stat-label">剩余：{{ myGovernance.today.remaining_requests ?? '不限' }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">今日已用 Token</span>
      <span class="app-stat-value">{{ myGovernance.today.used_tokens || 0 }}</span>
      <span class="app-stat-label">剩余：{{ myGovernance.today.remaining_tokens ?? '不限' }}</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">当前窗口请求数</span>
      <span class="app-stat-value">{{ myGovernance.rate_limit.current_requests || 0 }}</span>
      <span class="app-stat-label">限额：{{ myGovernance.rate_limit.max_requests || '未启用' }} / {{ myGovernance.rate_limit.window_seconds || 0 }}s</span>
    </div>
    <div class="app-stat">
      <span class="app-stat-label">今日拦截次数</span>
      <span class="app-stat-value">{{ myGovernance.today.blocked_requests || 0 }}</span>
      <span class="app-stat-label">预留输出：{{ myGovernance.policy.estimated_completion_tokens || 0 }} Token</span>
    </div>
  </div>

  <div class="app-panel-grid-2 token-block">
    <section class="app-card">
      <div class="app-card-head">
        <h2>按功能分类</h2>
        <span class="app-card-head-meta">共 {{ actionRows.length }} 项</span>
      </div>
      <div class="app-card-body">
        <div class="app-table-wrap">
          <el-table :data="actionRows" border size="small">
            <el-table-column prop="action" label="功能" />
            <el-table-column prop="calls" label="调用次数" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.calls }}</span></template>
            </el-table-column>
            <el-table-column prop="total_tokens" label="Token 数" width="120">
              <template #default="{ row }"><span class="app-num">{{ row.total_tokens }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
    <section class="app-card">
      <div class="app-card-head">
        <h2>每日使用趋势</h2>
        <span class="app-card-head-meta">近 {{ tokenDays }} 天</span>
      </div>
      <div class="app-card-body">
        <div class="app-table-wrap">
          <el-table :data="dateRows" border size="small" max-height="300">
            <el-table-column prop="date" label="日期" width="120" />
            <el-table-column prop="calls" label="调用次数" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.calls }}</span></template>
            </el-table-column>
            <el-table-column prop="total_tokens" label="Token 数" width="120">
              <template #default="{ row }"><span class="app-num">{{ row.total_tokens }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
  </div>

  <section v-if="isAdmin && globalStats.total_calls" class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>全局统计</h2>
      <span class="app-card-head-meta">近 {{ tokenDays }} 天</span>
    </div>
    <div class="app-card-body">
      <div class="app-metric-grid-4">
        <div class="app-stat">
          <span class="app-stat-label">总调用次数</span>
          <span class="app-stat-value">{{ globalStats.total_calls || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">总 Token 数</span>
          <span class="app-stat-value">{{ globalStats.total_tokens || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">输入 Token</span>
          <span class="app-stat-value">{{ globalStats.total_prompt_tokens || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">输出 Token</span>
          <span class="app-stat-value">{{ globalStats.total_completion_tokens || 0 }}</span>
        </div>
      </div>
      <div class="app-metric-grid-4 token-section">
        <div class="app-stat">
          <span class="app-stat-label">今日请求数</span>
          <span class="app-stat-value">{{ globalGovernance.today.total_requests || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">今日 Token</span>
          <span class="app-stat-value">{{ globalGovernance.today.total_tokens || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">今日拦截</span>
          <span class="app-stat-value">{{ globalGovernance.today.blocked_requests || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">分钟限流</span>
          <span class="app-stat-value">{{ globalGovernance.policy.rate_limit_max_requests || '未启用' }}<small>/ {{ globalGovernance.policy.rate_limit_window_seconds || 0 }}s</small></span>
        </div>
      </div>
      <div v-if="Object.keys(globalStats.by_model || {}).length" class="token-section">
        <h3 class="app-card-section-title">按模型：</h3>
        <div class="app-chip-row">
          <span v-for="(value, model) in globalStats.by_model" :key="model" class="app-tag is-info">
            {{ model }}: {{ value.calls }} 次 / {{ value.total_tokens }} Token
          </span>
        </div>
      </div>
    </div>
  </section>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>模型调用概览</h2>
      <span class="app-card-head-meta">近 {{ tokenDays }} 天 · 共 {{ llmTotal }} 条调用记录</span>
    </div>
    <div class="app-card-body">
      <div class="app-metric-grid-4">
        <div class="app-stat">
          <span class="app-stat-label">调用次数</span>
          <span class="app-stat-value">{{ llmStats.total_calls || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">失败次数</span>
          <span class="app-stat-value">{{ llmStats.failed_calls || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">平均耗时(ms)</span>
          <span class="app-stat-value">{{ llmStats.avg_duration_ms || 0 }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">成功率</span>
          <span class="app-stat-value">{{ Math.round((llmStats.success_rate || 0) * 100) }}<small>%</small></span>
        </div>
      </div>

      <div class="app-panel-grid-2 token-section">
        <div>
          <h3 class="app-card-section-title">按模块统计</h3>
          <div class="app-table-wrap">
            <el-table :data="llmModuleRows" size="small" border max-height="220">
              <el-table-column prop="module_name" label="模块" />
              <el-table-column prop="count" label="调用数" width="110">
                <template #default="{ row }"><span class="app-num">{{ row.count }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
        <div>
          <h3 class="app-card-section-title">按动作统计</h3>
          <div class="app-table-wrap">
            <el-table :data="llmActionRows" size="small" border max-height="220">
              <el-table-column prop="action" label="动作" />
              <el-table-column prop="calls" label="调用数" width="90">
                <template #default="{ row }"><span class="app-num">{{ row.calls }}</span></template>
              </el-table-column>
              <el-table-column prop="failed" label="失败数" width="90">
                <template #default="{ row }"><span class="app-num">{{ row.failed }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </div>

      <div class="app-panel-grid-2 token-section">
        <div>
          <h3 class="app-card-section-title">模型调用失败趋势</h3>
          <div class="app-table-wrap">
            <el-table :data="llmFailedDateRows" size="small" border max-height="220">
              <el-table-column prop="date" label="日期" width="140" />
              <el-table-column prop="count" label="失败数" width="100">
                <template #default="{ row }"><span class="app-num">{{ row.count }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
        <div>
          <h3 class="app-card-section-title">Agent 执行成功率</h3>
          <div class="app-metric-grid-3">
            <div class="app-stat">
              <span class="app-stat-label">总执行数</span>
              <span class="app-stat-value">{{ agentRunCount }}</span>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">成功数</span>
              <span class="app-stat-value">{{ agentSucceededCount }}</span>
            </div>
            <div class="app-stat">
              <span class="app-stat-label">成功率</span>
              <span class="app-stat-value">{{ Math.round(agentSuccessRate * 100) }}<small>%</small></span>
            </div>
          </div>
        </div>
      </div>

      <div class="token-section">
        <div class="app-table-wrap">
          <QueryStateView :status="llmStatus" :error="llmError" empty-text="暂无调用记录" min-height="200px" @retry="fetchTokenData">
          <el-table v-loading="llmLoading" :data="llmCalls" border size="small" max-height="360">
            <el-table-column prop="module_name" label="模块" width="100">
              <template #default="{ row }">{{ moduleLabel(row.module_name) }}</template>
            </el-table-column>
            <el-table-column prop="action" label="动作" width="180" show-overflow-tooltip />
            <el-table-column prop="model_name" label="模型" width="120" />
            <el-table-column prop="prompt_template" label="Prompt" width="160" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="90">
              <template #default="{ row }">
                <span class="app-tag" :class="row.status === 'success' ? 'is-ok' : 'is-danger'">
                  {{ row.status === 'success' ? '成功' : '失败' }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="duration_ms" label="耗时(ms)" width="100">
              <template #default="{ row }"><span class="app-num">{{ row.duration_ms }}</span></template>
            </el-table-column>
            <el-table-column prop="input_tokens" label="输入" width="80">
              <template #default="{ row }"><span class="app-num">{{ row.input_tokens }}</span></template>
            </el-table-column>
            <el-table-column prop="output_tokens" label="输出" width="80">
              <template #default="{ row }"><span class="app-num">{{ row.output_tokens }}</span></template>
            </el-table-column>
            <el-table-column prop="error_message" label="错误信息" show-overflow-tooltip />
            <el-table-column prop="created_at" label="时间" width="180" />
          </el-table>
          </QueryStateView>
        </div>
        <el-pagination
          background
          layout="total, prev, pager, next"
          :current-page="llmPage"
          :page-size="llmPageSize"
          :total="llmTotal"
          class="app-pagination-end"
          @current-change="handleLlmPageChange"
        />
      </div>
    </div>
  </section>

  <div class="app-panel-grid-2 token-block">
    <section class="app-card">
      <div class="app-card-head">
        <h2>成本统计</h2>
        <span class="app-card-head-meta">近 {{ tokenDays }} 天 · 共 {{ billingModelRows.length }} 个模型</span>
      </div>
      <div class="app-card-body">
        <div class="app-metric-grid-3">
          <div class="app-stat">
            <span class="app-stat-label">计费调用数</span>
            <span class="app-stat-value">{{ billingSummary.metered_calls || 0 }}</span>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">未映射模型</span>
            <span class="app-stat-value">{{ billingSummary.unpriced_calls || 0 }}</span>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">总成本({{ billingCurrency }})</span>
            <span class="app-stat-value">{{ billingSummary.total_cost || 0 }}</span>
          </div>
        </div>
        <div class="app-table-wrap token-section">
          <el-table :data="billingModelRows" border size="small" max-height="280">
            <el-table-column prop="model_name" label="模型" min-width="140" />
            <el-table-column prop="calls" label="调用数" width="90">
              <template #default="{ row }"><span class="app-num">{{ row.calls }}</span></template>
            </el-table-column>
            <el-table-column prop="priced_calls" label="计费数" width="90">
              <template #default="{ row }"><span class="app-num">{{ row.priced_calls }}</span></template>
            </el-table-column>
            <el-table-column prop="input_tokens" label="输入 Token" width="110">
              <template #default="{ row }"><span class="app-num">{{ row.input_tokens }}</span></template>
            </el-table-column>
            <el-table-column prop="output_tokens" label="输出 Token" width="110">
              <template #default="{ row }"><span class="app-num">{{ row.output_tokens }}</span></template>
            </el-table-column>
            <el-table-column :label="`成本(${billingCurrency})`" prop="total_cost" width="120">
              <template #default="{ row }"><span class="app-num">{{ row.total_cost }}</span></template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>
    <section class="app-card">
      <div class="app-card-head">
        <h2>模型价格表</h2>
        <span class="app-card-head-meta">共 {{ pricingRows.length }} 条</span>
      </div>
      <div class="app-card-body">
        <div class="app-table-wrap">
          <el-table :data="pricingRows" border size="small" max-height="280">
            <el-table-column prop="model_name" label="模型" min-width="140" />
            <el-table-column :label="`输入 / 1K(${billingCurrency})`" prop="input_per_1k" width="130">
              <template #default="{ row }"><span class="app-num">{{ row.input_per_1k }}</span></template>
            </el-table-column>
            <el-table-column :label="`输出 / 1K(${billingCurrency})`" prop="output_per_1k" width="130">
              <template #default="{ row }"><span class="app-num">{{ row.output_per_1k }}</span></template>
            </el-table-column>
          </el-table>
        </div>
        <div v-if="billingSummary.unmapped_models?.length" class="token-note">
          未映射模型：{{ billingSummary.unmapped_models.join('、') }}
        </div>
      </div>
    </section>
  </div>

  <section class="app-card system-panel-card">
    <div class="app-card-head">
      <h2>问答回放</h2>
      <span class="app-card-head-meta">共 {{ qaReplayRows.length }} 条</span>
    </div>
    <div class="app-card-body is-flush">
      <div class="app-table-wrap">
        <el-table :data="qaReplayRows" border size="small" max-height="360">
          <el-table-column prop="document_title" label="文档" min-width="140" show-overflow-tooltip />
          <el-table-column prop="question" label="问题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="source" label="来源" width="100">
            <template #default="{ row }">
              <span class="app-tag is-info">{{ qaSourceLabel(row.source) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="model_name" label="模型" width="120" />
          <el-table-column prop="latency_ms" label="耗时(ms)" width="100">
            <template #default="{ row }"><span class="app-num">{{ row.latency_ms }}</span></template>
          </el-table-column>
          <el-table-column prop="feedback_status" label="反馈状态" width="100">
            <template #default="{ row }">
              <span v-if="row.feedback_status === 'open'" class="app-tag is-pending">待处理</span>
              <span v-else-if="row.feedback_status === 'resolved'" class="app-tag is-ok">已处理</span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间" width="180" />
          <el-table-column label="操作" width="100" fixed="right">
            <template #default="{ row }">
              <el-button text type="primary" @click="openQaReplay(row)">回放</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </section>

  <el-dialog v-model="qaReplayVisible" title="问答回放" width="760px" class="system-dialog" append-to-body>
    <template v-if="selectedQaReplay">
      <div class="dialog-stack">
        <div><strong>文档：</strong>{{ selectedQaReplay.document_title || `#${selectedQaReplay.document_id}` }}</div>
        <div><strong>问题：</strong>{{ selectedQaReplay.question }}</div>
        <div class="dialog-answer">
          <strong>回答：</strong>
          <div class="dialog-pre">{{ selectedQaReplay.answer }}</div>
        </div>
        <div><strong>命中引用：</strong></div>
        <el-empty v-if="!selectedQaReplay.citations?.length" description="暂无引用" />
        <div v-for="(item, index) in (selectedQaReplay.citations || [])" :key="`qa-replay-citation-${index}`" class="citation-card">
          <div class="dialog-stack">
            <div class="app-chip-row">
              <span class="app-tag is-info">片段 {{ (item.chunk_index ?? index) + 1 }}</span>
              <span>{{ item.section_title || '未标注章节' }}</span>
              <span v-if="item.page_number" class="citation-page">第 {{ item.page_number }} 页</span>
            </div>
            <blockquote class="citation-quote">{{ item.source_text || '暂无原文片段' }}</blockquote>
          </div>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemTokenAnalytics } from '../../composables/useSystemTokenAnalytics'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  tokenDays, myStats, globalStats, llmPage, llmPageSize, llmTotal, llmStats, llmCalls,
  llmLoading, llmError, billingStats, qaReplayRows, qaReplayVisible, selectedQaReplay, fetchTokenData,
  openQaReplay, resetTokenPagination, handleLlmPageChange,
} = useSystemTokenAnalytics({ client: api, message: ElMessage, isAdmin })

const { agentRunCount, agentSucceededCount, agentSuccessRate } = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin })

// 三态（ux-audit M-13）：仅包裹主结果表 llmCalls；多表共用一次 fetch，保留原有 message.error 以覆盖其它表
const llmStatus = computed(() => {
  if (llmLoading.value && !llmCalls.value.length) return 'loading'
  if (llmError.value) return 'error'
  if (!llmCalls.value.length) return 'empty'
  return 'success'
})

const emptyGovernance = () => ({ today: {}, rate_limit: {}, policy: {} })
const myGovernance = computed(() => ({ ...emptyGovernance(), ...(myStats.value.governance || {}) }))
const globalGovernance = computed(() => ({ today: {}, policy: {}, ...(globalStats.value.governance || {}) }))

const actionRows = computed(() => {
  const byAction = myStats.value.by_action || {}
  const labels = {
    chat: '聊天',
    chat_stream: '聊天流式',
    rag_answer: '文档问答',
    rag_chat_stream: '文档问答流式',
    document_summary: '文档摘要',
    document_risk_extract: '文档风险提取',
    document_todo_extract: '文档待办提取',
    document_clause_extract: '文档条款提取',
    document_compare: '文档对比',
    legal_consultation: '法律咨询',
    agent_plan: 'Agent 规划',
    task_extract_from_chat: '聊天待办提取',
    task_decompose: '任务拆解',
  }
  return Object.entries(byAction)
    .map(([action, value]) => ({
      action: labels[action] || action,
      action_key: action,
      calls: value.calls,
      total_tokens: value.total_tokens,
    }))
    .sort((a, b) => b.total_tokens - a.total_tokens)
})

const dateRows = computed(() => {
  const byDate = myStats.value.by_date || {}
  return Object.entries(byDate)
    .map(([date, value]) => ({ date, calls: value.calls, total_tokens: value.total_tokens }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const llmModuleRows = computed(() => {
  const byModule = llmStats.value.by_module || {}
  return Object.entries(byModule)
    .map(([module_name, count]) => ({ module_name, count }))
    .sort((a, b) => b.count - a.count)
})

const llmActionRows = computed(() => {
  const byAction = llmStats.value.by_action || {}
  return Object.entries(byAction)
    .map(([action, value]) => ({ action, calls: value.calls, failed: value.failed }))
    .sort((a, b) => b.calls - a.calls)
})

const llmFailedDateRows = computed(() => {
  const byDate = llmStats.value.failed_by_date || {}
  return Object.entries(byDate)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))
})

const billingSummary = computed(() => billingStats.value.summary || {})
const billingCurrency = computed(() => billingStats.value.summary?.currency || billingStats.value.pricing?.currency || 'CNY')
const pricingRows = computed(() => billingStats.value.pricing?.items || [])
const billingModelRows = computed(() => {
  const byModel = billingStats.value.by_model || {}
  return Object.entries(byModel)
    .map(([model_name, value]) => ({
      model_name,
      calls: value.calls || 0,
      priced_calls: value.priced_calls || 0,
      input_tokens: value.input_tokens || 0,
      output_tokens: value.output_tokens || 0,
      total_cost: value.total_cost || 0,
    }))
    .sort((a, b) => b.total_cost - a.total_cost)
})

const moduleLabel = (mod) => ({
  document: '文档',
  legal: '法律文书',
  task: '任务',
  agent: 'Agent',
  async_task: '异步任务',
  system: '系统',
  chat: '聊天',
  prompt: '提示词',
  auth: '认证',
}[mod] || mod)

const qaSourceLabel = (source) => ({
  document: '文档问答',
  chat: '聊天问答',
  ws_chat: '流式问答',
}[source] || source || '未知')

onMounted(async () => {
  await authStore.loadMe()
  fetchTokenData()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
/* 页面级数据块（指标网格 / 双卡并排）之间的统一间距 */
.token-block {
  margin-top: var(--space-4);
}
/* 卡片内的次级分区（指标网格、子表、按模型标签）之间的统一间距 */
.token-section {
  margin-top: var(--space-5);
}
.token-note {
  margin-top: var(--space-3);
  color: var(--color-warning);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.dialog-stack {
  display: grid;
  gap: var(--space-2);
}
.dialog-answer {
  display: grid;
  gap: var(--space-2);
}
.dialog-pre {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-bg-alt);
}
.citation-quote {
  margin: 0;
  color: var(--color-text);
}
.citation-card {
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--color-surface-subtle);
}
.citation-page {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.system-dialog :deep(.el-dialog) {
  border-radius: var(--radius-xl);
}
</style>
