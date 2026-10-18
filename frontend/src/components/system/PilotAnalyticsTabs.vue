<template>
  <div class="pilot-analytics-tabs">
    <template v-if="section !== 'retention'">
      <div class="app-section-intro tab-intro">
        <strong>试点转化漏斗（P-1）</strong>
        <span>注册 → 首次咨询 → 首次审查 → 首次文书 → 首次审核通过 → 升级付费，定位最大流失点。数据由既有业务表推导，可追溯历史，无需额外埋点。</span>
      </div>

      <section class="app-card">
        <div class="app-card-body">
          <div class="app-filter-bar">
            <span class="app-filter-label">注册队列窗口：</span>
            <el-radio-group v-model="funnelDays" @change="fetchFunnel">
              <el-radio-button :value="7">近 7 天</el-radio-button>
              <el-radio-button :value="30">近 30 天</el-radio-button>
              <el-radio-button :value="90">近 90 天</el-radio-button>
            </el-radio-group>
            <div class="app-filter-bar-actions">
              <el-button :loading="funnelLoading" @click="fetchFunnel">刷新</el-button>
            </div>
          </div>
        </div>
      </section>

      <div class="app-metric-grid-4 funnel-metric-grid">
        <div class="app-stat">
          <span class="app-stat-label">队列注册</span>
          <strong class="app-stat-value">{{ funnelData?.cohort?.registered || 0 }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">激活·首次咨询</span>
          <strong class="app-stat-value">{{ funnelData?.activation?.cohort_users_with_consultation || 0 }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">注册→首次咨询(天)</span>
          <strong class="app-stat-value">{{ funnelData?.activation?.avg_days_reg_to_first_consult ?? '—' }}</strong>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">升级付费</span>
          <strong class="app-stat-value">{{ funnelUpgradedUsers }}</strong>
        </div>
      </div>

      <section class="app-card">
        <div class="app-card-head">
          <h2>阶段明细</h2>
          <span class="app-card-head-meta">近 {{ funnelDays }} 天 · 共 {{ funnelRows.length }} 个阶段</span>
        </div>
        <div class="app-card-body is-flush">
          <ol class="funnel-steps">
            <li v-for="row in funnelRows" :key="row.stage" class="app-thread is-static">
              <span class="app-thread-title">{{ row.label }}</span>
              <span class="app-thread-meta">
                <span class="app-step-metric">
                  <span class="app-step-label">人数</span>
                  <span class="app-num">{{ row.users }}</span>
                </span>
                <span class="app-step-metric">
                  <span class="app-step-label">上一跳转化</span>
                  <span class="app-num">{{ row.stage === 'registered' ? '—' : `${row.hop_pct}%` }}</span>
                </span>
              </span>
            </li>
          </ol>
          <div class="app-table-wrap">
            <el-table v-loading="funnelLoading" :data="funnelRows" border size="small">
              <el-table-column prop="label" label="阶段" width="150" />
              <el-table-column prop="users" label="人数" width="90" class-name="app-num" />
              <el-table-column label="占注册比例" width="120">
                <template #default="{ row }"><span class="app-num">{{ row.overall_pct }}%</span></template>
              </el-table-column>
              <el-table-column label="上一跳转化" width="120">
                <template #default="{ row }">
                  <span v-if="row.stage === 'registered'">—</span>
                  <span v-else class="app-num">{{ row.hop_pct }}%</span>
                </template>
              </el-table-column>
              <el-table-column label="相对占比">
                <template #default="{ row }"><span class="app-num">{{ row.width }}%</span></template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </section>
    </template>

    <template v-if="section !== 'funnel'">
      <div class="app-section-intro tab-intro">
        <strong>北极星指标 + 新用户留存（P-5）</strong>
        <span>按周跟踪活跃律师与案件闭环信号；按注册周分群计算 7 日 / 30 日留存。未完全观察的窗口显示「—」。</span>
      </div>

      <section class="app-card">
        <div class="app-card-body">
          <div class="app-filter-bar">
            <span class="app-filter-label">北极星周期：</span>
            <el-radio-group v-model="northStarWeeks" @change="fetchNorthStar">
              <el-radio-button :value="4">近 4 周</el-radio-button>
              <el-radio-button :value="12">近 12 周</el-radio-button>
              <el-radio-button :value="26">近 26 周</el-radio-button>
            </el-radio-group>
            <div class="app-filter-bar-actions">
              <el-button :loading="northStarLoading" @click="fetchNorthStar">刷新</el-button>
            </div>
          </div>
        </div>
      </section>

      <div class="app-metric-grid-4 northstar-metric-grid">
        <div class="app-stat">
          <span class="app-stat-label">本周活跃律师</span>
          <strong class="app-stat-value">{{ northStarCurrent.active_lawyers }}</strong>
          <span class="app-stat-delta" :class="deltaClass(northStarChangePct.active_lawyers)">{{ deltaText(northStarChangePct.active_lawyers) }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">持进行中案件</span>
          <strong class="app-stat-value">{{ northStarCurrent.with_active_case }}</strong>
          <span class="app-stat-delta" :class="deltaClass(northStarChangePct.with_active_case)">{{ deltaText(northStarChangePct.with_active_case) }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">本周任务数</span>
          <strong class="app-stat-value">{{ northStarCurrent.tasks }}</strong>
          <span class="app-stat-delta" :class="deltaClass(northStarChangePct.tasks)">{{ deltaText(northStarChangePct.tasks) }}</span>
        </div>
        <div class="app-stat">
          <span class="app-stat-label">案件内任务</span>
          <strong class="app-stat-value">{{ northStarCurrent.case_tasks }}</strong>
          <span class="app-stat-delta" :class="deltaClass(northStarChangePct.case_tasks)">{{ deltaText(northStarChangePct.case_tasks) }}</span>
        </div>
      </div>

      <section class="app-card">
        <div class="app-card-head">
          <h2>周度指标</h2>
          <span class="app-card-head-meta">近 {{ northStarWeeks }} 周 · 共 {{ northStarWeekly.length }} 周</span>
        </div>
        <div class="app-card-body is-flush">
          <div class="app-table-wrap">
            <el-table v-loading="northStarLoading" :data="northStarWeekly" border size="small">
              <el-table-column prop="week_start" label="周（周一首）" width="130" />
              <el-table-column prop="active_lawyers" label="活跃律师" width="110" class-name="app-num" />
              <el-table-column prop="with_active_case" label="持进行中案件" width="130" class-name="app-num" />
              <el-table-column prop="tasks" label="任务数" width="90" class-name="app-num" />
              <el-table-column prop="case_tasks" label="案件内任务" width="110" class-name="app-num" />
            </el-table>
          </div>
        </div>
      </section>

      <section class="app-card">
        <div class="app-card-body">
          <div class="app-filter-bar">
            <span class="app-filter-label">留存窗口：</span>
            <el-radio-group v-model="retentionDays" @change="fetchRetention">
              <el-radio-button :value="30">近 30 天</el-radio-button>
              <el-radio-button :value="90">近 90 天</el-radio-button>
            </el-radio-group>
            <div class="app-filter-bar-actions">
              <el-button :loading="retentionLoading" @click="fetchRetention">刷新</el-button>
            </div>
          </div>
        </div>
      </section>

      <section class="app-card">
        <div class="app-card-head">
          <h2>注册周留存</h2>
          <span class="app-card-head-meta">近 {{ retentionDays }} 天 · 共 {{ retentionCohorts.length }} 个注册周</span>
        </div>
        <div class="app-card-body is-flush">
          <div class="app-table-wrap">
            <el-table v-loading="retentionLoading" :data="retentionCohorts" border size="small">
              <el-table-column prop="week_start" label="注册周" width="130" />
              <el-table-column prop="cohort_size" label="新用户" width="90" class-name="app-num" />
              <el-table-column label="D7 留存" width="160">
                <template #default="{ row }"><span class="app-num">{{ rateText(row.d7) }}</span></template>
              </el-table-column>
              <el-table-column label="D30 留存" width="160">
                <template #default="{ row }"><span class="app-num">{{ rateText(row.d30) }}</span></template>
              </el-table-column>
              <el-table-column label="说明" min-width="240">
                <template #default="{ row }">{{ windowNote(row) }}</template>
              </el-table-column>
            </el-table>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/button/style/css'
import api from '../../api'
import { useSystemFunnel } from '../../composables/useSystemFunnel'
import { useSystemRetention } from '../../composables/useSystemRetention'

const {
  funnelDays, funnelData, funnelLoading, funnelRows, funnelUpgradedUsers, fetchFunnel,
} = useSystemFunnel({ client: api, message: ElMessage })
const {
  retentionDays, retentionData, retentionLoading, fetchRetention,
  northStarWeeks, northStarData, northStarLoading, fetchNorthStar,
} = useSystemRetention({ client: api, message: ElMessage })

const props = defineProps({
  section: { type: String, default: 'all' },
})

onMounted(async () => {
  const jobs = []
  if (props.section !== 'retention') jobs.push(fetchFunnel())
  if (props.section !== 'funnel') jobs.push(fetchRetention(), fetchNorthStar())
  await Promise.allSettled(jobs)
})

const northStarCurrent = computed(() => northStarData.value?.current || { active_lawyers: 0, with_active_case: 0, tasks: 0, case_tasks: 0 })
const northStarChangePct = computed(() => northStarData.value?.weekly_change_pct || {})
const northStarWeekly = computed(() => northStarData.value?.weekly || [])
const retentionCohorts = computed(() => retentionData.value?.cohorts || [])

const deltaText = (pct) => (pct == null ? '较上周 —' : `较上周 ${pct >= 0 ? '+' : ''}${pct}%`)
const deltaClass = (pct) => {
  if (pct == null) return 'is-muted'
  return pct >= 0 ? 'is-up' : 'is-down'
}
const rateText = (metric) => {
  if (!metric || metric.rate == null) return '—（未完全观察）'
  return `${(metric.rate * 100).toFixed(1)}%（${metric.active}/${metric.observed}）`
}
const windowNote = (row) => {
  const parts = []
  if (!row.d7 || row.d7.observed === 0) parts.push('D7 窗口未经历')
  if (!row.d30 || row.d30.observed === 0) parts.push('D30 窗口未经历')
  return parts.length ? parts.join('；') : 'D7/D30 窗口均已完全观察'
}
</script>

<style scoped>
.pilot-analytics-tabs {
  display: grid;
  gap: var(--space-4);
}
.app-stat-delta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.app-stat-delta.is-up {
  color: var(--color-success);
}
.app-stat-delta.is-down {
  color: var(--color-danger);
}
/* 漏斗步骤条：步骤 → 人数 → 上一跳转化，放 flush body 首行，表格贴卡片边 */
.funnel-steps {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border-light);
  list-style: none;
}
.app-thread.is-static {
  cursor: default;
  border-color: var(--color-border-light);
}
.app-thread.is-static:hover {
  border-color: var(--color-border-light);
  background: var(--color-surface);
}
.app-step-metric {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
}
.app-step-label {
  color: var(--color-text-muted);
}
.app-step-metric .app-num {
  color: var(--color-text);
  font-weight: 600;
}
/* 令牌里的 .app-metric-grid-4 在 1100px 以下直接收成一列，四个读数用两列更合适 */
@media (max-width: 1100px) {
  .funnel-metric-grid,
  .northstar-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
