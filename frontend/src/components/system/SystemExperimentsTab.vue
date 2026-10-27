<template>
  <template v-if="!isAdmin">
    <div class="app-readonly-banner system-panel-card">
      <strong>仅管理员可见</strong>
      <span>实验观测面板包含 Prompt 灰度与评测数据，当前账号无权限查看。</span>
    </div>
  </template>
  <template v-else>
    <div class="app-section-intro tab-intro">
      <strong>实验与灰度观测</strong>
      <span>评估 Prompt 版本效果、流量覆盖情况和基线回退风险。</span>
    </div>

    <section class="app-card system-panel-card">
      <div class="app-card-body">
        <div class="app-filter-bar">
          <span class="app-filter-label">流量窗口：</span>
          <el-radio-group v-model="experimentDays" @change="refreshExperimentWindow">
            <el-radio-button :value="7">近 7 天</el-radio-button>
            <el-radio-button :value="30">近 30 天</el-radio-button>
            <el-radio-button :value="90">近 90 天</el-radio-button>
          </el-radio-group>
          <div class="app-filter-bar-actions">
            <el-button :loading="experimentLoading || evidenceLoading" @click="refreshExperimentWindow">刷新</el-button>
          </div>
        </div>
      </div>
    </section>

    <div class="app-metric-grid-4 experiment-metric-grid">
      <div class="app-stat">
        <span class="app-stat-label">评测样本数</span>
        <strong class="app-stat-value">{{ experimentSummary.dataset_size || 0 }}</strong>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">实验数量</span>
        <strong class="app-stat-value">{{ experimentSummary.experiment_count || 0 }}</strong>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">回退风险实验</span>
        <strong class="app-stat-value">{{ experimentSummary.degraded_experiment_count || 0 }}</strong>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">灰度中的 Prompt</span>
        <strong class="app-stat-value">{{ rolloutSummary.active_rollout_count || 0 }}</strong>
      </div>
    </div>

    <section class="app-card system-panel-card">
      <div class="app-card-head">
        <h2>评测产物状态</h2>
        <span class="app-card-head-meta">共 {{ experimentArtifactRows.length }} 项</span>
      </div>
      <div class="app-card-body">
        <div class="app-metric-grid-4 artifact-grid">
          <div v-for="item in experimentArtifactRows" :key="item.key" class="app-stat">
            <span class="app-stat-label">{{ item.label }}</span>
            <span class="app-stat-value" :class="{ 'app-num': item.mono }">
              <span v-if="item.tagged" class="app-tag" :class="item.ok ? 'is-ok' : 'is-pending'">{{ item.text }}</span>
              <template v-else>{{ item.text }}</template>
            </span>
          </div>
        </div>
      </div>
    </section>

    <section class="app-card system-panel-card">
      <div class="app-card-head">
        <h2>证据可核验性（在线指标）</h2>
        <span class="app-card-head-meta">
          近 {{ evidenceStats.window_days }} 天 · {{ evidenceStats.total_consultations }} 条咨询答复，其中 {{ evidenceStats.with_evidence }} 条带指标（生成时 {{ evidenceStats.with_evidence_generated }} / 事后重算 {{ evidenceStats.with_evidence_recomputed }}）
        </span>
      </div>
      <div class="app-card-body">
        <div class="evidence-actions">
          <el-button size="small" @click="openEvidenceIssues({})">查看引用问题</el-button>
        </div>
        <div class="app-metric-grid-4 evidence-metric-grid">
          <div class="app-stat">
            <span class="app-stat-label">逐条核对通过率</span>
            <strong class="app-stat-value">{{ formatRate(evidenceStats.checked_rate) }}</strong>
            <span class="app-stat-note">{{ evidenceStats.references?.article_verified || 0 }} / {{ evidenceStats.references?.article_checked || 0 }} 条引用核到条文</span>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">指标覆盖面</span>
            <strong class="app-stat-value">{{ formatRate(evidenceStats.evidence_coverage) }}</strong>
            <span class="app-stat-note">{{ evidenceStats.without_evidence }} 条老记录无指标（不摊进分母）</span>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">引用条数</span>
            <strong class="app-stat-value">{{ evidenceStats.references?.references || 0 }}</strong>
            <span class="app-stat-note">{{ evidenceStats.references?.article_missing || 0 }} 条条号未找到</span>
          </div>
          <div class="app-stat">
            <span class="app-stat-label">语料缺口引用</span>
            <strong class="app-stat-value">{{ evidenceStats.references?.no_corpus || 0 }}</strong>
            <span class="app-stat-note">{{ evidenceStats.references?.no_numbers || 0 }} 条未标条号 · {{ evidenceStats.references?.unmatched || 0 }} 条未挂法源</span>
          </div>
        </div>
        <div class="app-table-wrap evidence-level-wrap">
          <el-table v-loading="evidenceLoading" :data="evidenceLevelRows" border size="small">
            <el-table-column prop="label" label="可核验性口径" min-width="160" />
            <el-table-column prop="count" label="答复数" width="100" class-name="app-num" />
            <el-table-column prop="share_text" label="占比" width="100" class-name="app-num" />
          </el-table>
        </div>

        <h3 class="evidence-subhead">按天趋势</h3>
        <p v-if="!evidenceTrendRows.length" class="evidence-empty">近 {{ evidenceStats.window_days }} 天没有带指标的答复。</p>
        <div v-else class="app-table-wrap">
          <el-table :data="evidenceTrendRows" border size="small" max-height="260">
            <el-table-column prop="date" label="日期" width="110" />
            <el-table-column prop="total" label="答复" width="80" class-name="app-num" />
            <el-table-column prop="with_evidence" label="带指标" width="90" class-name="app-num" />
            <el-table-column prop="with_evidence_recomputed" label="事后重算" width="100" class-name="app-num" />
            <el-table-column prop="strong" label="逐条核对" width="100" class-name="app-num" />
            <el-table-column prop="partial" label="部分核对" width="100" class-name="app-num" />
            <el-table-column prop="weak" label="待核" width="80" class-name="app-num" />
            <el-table-column prop="none" label="未引用" width="80" class-name="app-num" />
            <el-table-column prop="checked_text" label="条文核对通过率" min-width="130" class-name="app-num" />
          </el-table>
        </div>

        <h3 class="evidence-subhead">
          法源引用与语料缺口
          <span class="app-card-head-meta">
            窗口内共 {{ evidenceStats.sources_total || 0 }} 个法源被引用<span
              v-if="(evidenceStats.sources_total || 0) > (evidenceStats.sources || []).length"
            >（下表只列引用最多的 {{ (evidenceStats.sources || []).length }} 个）</span>
          </span>
        </h3>
        <p v-if="!evidenceSourceRows.length" class="evidence-empty">窗口内的答复没有挂到法源库的引用。</p>
        <div v-else class="app-table-wrap">
          <el-table :data="evidenceSourceRows" border size="small" max-height="320">
            <el-table-column label="法源" min-width="220">
              <template #default="{ row }">
                <div class="evidence-source-title">
                  <span :title="row.title">{{ row.title }}</span>
                  <el-button text size="small" @click="openEvidenceIssues({ source: row })">查看</el-button>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="references" label="被引用" width="90" class-name="app-num" />
            <el-table-column prop="consultations" label="涉及答复" width="100" class-name="app-num" />
            <el-table-column prop="articles" label="条文数" width="90" class-name="app-num" />
            <el-table-column prop="verified" label="核验通过" width="100" class-name="app-num" />
            <el-table-column label="语料" width="110">
              <template #default="{ row }">
                <span v-if="row.articles" class="app-tag is-ok">有条文</span>
                <span v-else class="app-tag is-warn">缺条文</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <p v-if="evidenceGapNote" class="evidence-note">{{ evidenceGapNote }}</p>

        <div v-if="issuesVisible" class="evidence-issues">
          <div class="evidence-issues-head">
            <h3 class="evidence-subhead">{{ evidenceIssuesTitle }}</h3>
            <el-radio-group v-model="issuesFilter" size="small" @change="loadEvidenceIssues">
              <el-radio-button value="all">全部问题</el-radio-button>
              <el-radio-button value="missing">条号未找到</el-radio-button>
              <el-radio-button value="no_corpus">法源缺条文</el-radio-button>
              <el-radio-button value="unmatched">未挂法源</el-radio-button>
              <el-radio-button value="weak">待核答复</el-radio-button>
            </el-radio-group>
            <el-button text size="small" @click="issuesVisible = false">收起</el-button>
          </div>
          <p class="evidence-note">
            窗口内匹配 {{ evidenceIssues.total }} 条，最多显示 50 条；展开行看引用明细。
          </p>
          <QueryStateView
            :status="issuesStatus"
            :error="evidenceIssuesError"
            empty-text="没有匹配的引用问题"
            min-height="160px"
            @retry="loadEvidenceIssues"
          >
            <div class="app-table-wrap">
              <el-table v-loading="evidenceIssuesLoading" :data="evidenceIssueRows" border size="small" max-height="380">
                <el-table-column type="expand">
                  <template #default="{ row }">
                    <div class="issue-ref-list">
                      <div v-for="(refItem, index) in row.references" :key="index" class="issue-ref">
                        <div class="issue-ref-head">
                          <strong>{{ refItem.title }}</strong>
                          <span class="app-tag" :class="refItem.scope === 'verified' ? 'is-ok' : 'is-warning'">
                            {{ refItem.scope_label }}
                          </span>
                          <span v-if="refItem.citation" class="issue-citation">{{ refItem.citation }}</span>
                        </div>
                        <p v-if="refItem.note" class="muted">{{ refItem.note }}</p>
                      </div>
                      <p v-if="!row.references.length" class="muted">这条答复没有引用明细。</p>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column prop="time_text" label="时间" width="140" />
                <el-table-column prop="question" label="咨询问题" min-width="240" show-overflow-tooltip />
                <el-table-column label="可核验性" width="120">
                  <template #default="{ row }">
                    <span class="app-tag" :class="row.level_class">{{ row.level_label }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="issue_text" label="问题" width="180" />
              </el-table>
            </div>
          </QueryStateView>
        </div>
      </div>
    </section>

    <section class="app-card system-panel-card">
      <div class="app-card-head">
        <h2>实验结果</h2>
        <span class="app-card-head-meta">共 {{ experimentRows.length }} 个实验 · 近 {{ experimentDays }} 天</span>
      </div>
      <div class="app-card-body">
        <QueryStateView :status="experimentStatus" :error="experimentError" empty-text="暂无实验" min-height="200px" @retry="fetchExperimentOverview">
          <div class="app-table-wrap">
            <el-table v-loading="experimentLoading" :data="experimentRows" border size="small" max-height="420">
              <el-table-column prop="name" label="实验" width="150" show-overflow-tooltip />
              <el-table-column prop="prompt_label" label="Prompt" width="140" show-overflow-tooltip />
              <el-table-column prop="top_k" label="TopK" width="70" class-name="app-num" />
              <el-table-column prop="citation_accuracy_text" label="Citation" width="100" class-name="app-num" />
              <el-table-column prop="hit_at_k_text" label="Hit@K" width="90" class-name="app-num" />
              <el-table-column prop="refusal_accuracy_text" label="Refusal" width="90" class-name="app-num" />
              <el-table-column prop="badcase_count" label="Badcase" width="90" class-name="app-num" />
              <el-table-column label="对基线">
                <template #default="{ row }">
                  <span v-if="row.regression_metrics?.length" class="app-tag is-danger">{{ row.regression_metrics.join(', ') }}</span>
                  <span v-else class="app-tag is-ok">无回退</span>
                </template>
              </el-table-column>
              <el-table-column label="配置漂移" width="90">
                <template #default="{ row }"><span class="app-num">{{ row.config_drift_count }}</span></template>
              </el-table-column>
            </el-table>
          </div>
        </QueryStateView>
      </div>
    </section>

    <section class="app-card system-panel-card">
      <div class="app-card-head">
        <h2>灰度发布</h2>
        <span class="app-card-head-meta">共 {{ rolloutRows.length }} 个模板</span>
      </div>
      <div class="app-card-body is-flush">
        <div class="app-table-wrap">
          <el-table v-loading="experimentLoading" :data="rolloutRows" border size="small" max-height="420">
            <el-table-column prop="name" label="模板" min-width="150" show-overflow-tooltip />
            <el-table-column prop="stable_version" label="稳定版" width="80" class-name="app-num" />
            <el-table-column prop="rollout_version" label="灰度版" width="80" class-name="app-num" />
            <el-table-column prop="percentage_text" label="比例" width="80" class-name="app-num" />
            <el-table-column prop="started_at" label="开始时间" width="180" />
          </el-table>
        </div>
      </div>
    </section>

    <section class="app-card system-panel-card">
      <div class="app-card-head">
        <h2>Prompt 实际流量覆盖</h2>
        <span class="app-card-head-meta">共 {{ promptTrafficRows.length }} 个 Prompt</span>
      </div>
      <div class="app-card-body is-flush">
        <div class="app-table-wrap">
          <el-table v-loading="experimentLoading" :data="promptTrafficRows" border size="small" max-height="420">
            <el-table-column prop="prompt_template" label="Prompt" min-width="160" show-overflow-tooltip />
            <el-table-column prop="prompt_version" label="版本" width="80">
              <template #default="{ row }"><span class="app-num">v{{ row.prompt_version }}</span></template>
            </el-table-column>
            <el-table-column prop="calls" label="调用数" width="90" class-name="app-num" />
            <el-table-column prop="failed_calls" label="失败数" width="90" class-name="app-num" />
            <el-table-column prop="success_rate_text" label="成功率" width="90" class-name="app-num" />
            <el-table-column prop="last_called_at" label="最近命中" width="180" />
          </el-table>
        </div>
      </div>
    </section>
  </template>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import api from '../../api'
import { useSystemObservability } from '../../composables/useSystemObservability'
import { useAuthStore } from '../../stores/auth'
import QueryStateView from '../QueryStateView.vue'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  experimentDays,
  experimentLoading,
  experimentOverview,
  experimentError,
  evidenceLoading,
  evidenceStats,
  evidenceError,
  evidenceIssuesLoading,
  evidenceIssues,
  evidenceIssuesError,
  fetchExperimentOverview,
  fetchEvidenceStats,
  fetchEvidenceIssues,
} = useSystemObservability({ client: api, message: ElMessage, isAdmin })

// 证据可核验性：口径四档（后端给 label 与占比），空窗口时给一行说明而不是空表
const evidenceLevelRows = computed(() => {
  if (evidenceError.value) return []
  return (evidenceStats.value.levels || []).map((row) => ({
    level: row.level,
    label: row.label,
    count: row.count,
    share_text: formatRate(row.share),
  }))
})

// 按天趋势：只有有数据的日期（后端已排序），通过率按「真的做过条文级核对的引用」算
const evidenceTrendRows = computed(() => {
  if (evidenceError.value) return []
  return (evidenceStats.value.trend || []).map((row) => ({
    date: row.date,
    total: row.total,
    with_evidence: row.with_evidence,
    with_evidence_recomputed: row.with_evidence_recomputed || 0,
    strong: row.strong,
    partial: row.partial,
    weak: row.weak,
    none: row.none,
    checked_text: formatRate(row.article_checked ? row.article_verified / row.article_checked : null),
  }))
})

// 法源引用榜：`articles === 0` 就是语料采购清单（模板类法源本就不该有条文）
const evidenceSourceRows = computed(() => {
  if (evidenceError.value) return []
  return evidenceStats.value.sources || []
})

const evidenceGapNote = computed(() => {
  const gaps = evidenceStats.value.corpus_gaps || []
  const unmatched = evidenceStats.value.unmatched_references || {}
  const parts = []
  if (gaps.length) {
    parts.push(`语料缺口 ${gaps.length} 个：${gaps.map((row) => row.title).join('、')}`)
  }
  if (unmatched.references) {
    parts.push(`${unmatched.references} 条引用没挂到法源库（涉及 ${unmatched.consultations} 条答复），未计入上表`)
  }
  return parts.join('；')
})

// 引用质量问题下钻：从聚合卡片展开到具体答复，再按法源/问题类型收窄
const issuesVisible = ref(false)
const issuesFilter = ref('all')
const issuesSource = ref(null)
const formatTime = (value) => (value ? String(value).replace('T', ' ').slice(0, 16) : '-')
const evidenceIssueRows = computed(() => (evidenceIssues.value.items || []).map((row) => ({
  ...row,
  time_text: formatTime(row.created_at),
  issue_text: (row.issue_labels || []).join(' / ') || '引用已核对',
  level_class: row.level === 'strong' ? 'is-ok' : (row.level === 'weak' || row.level === 'none' ? 'is-danger' : 'is-warning'),
})))
const evidenceIssuesTitle = computed(() => (
  issuesSource.value ? `引用质量：${issuesSource.value.title}` : '引用质量问题清单'
))
const issuesStatus = computed(() => {
  if (evidenceIssuesLoading.value && !evidenceIssueRows.value.length) return 'loading'
  if (evidenceIssuesError.value) return 'error'
  if (!evidenceIssueRows.value.length) return 'empty'
  return 'success'
})
const loadEvidenceIssues = () => {
  const params = { limit: 50 }
  if (issuesFilter.value !== 'all') params.issue = issuesFilter.value
  if (issuesSource.value?.source_id) params.source_id = issuesSource.value.source_id
  fetchEvidenceIssues(params)
}
const openEvidenceIssues = ({ source = null, issue = 'all' } = {}) => {
  issuesSource.value = source
  issuesFilter.value = issue
  issuesVisible.value = true
  loadEvidenceIssues()
}

const experimentArtifacts = computed(() => experimentOverview.value.artifact_status || {})
const experimentSummary = computed(() => experimentOverview.value.summary || {})
const rolloutSummary = computed(() => experimentOverview.value.rollouts || { items: [] })
const experimentRows = computed(() => {
  const rows = experimentOverview.value.experiments || []
  return rows.map((row) => {
    const config = row.effective_config || {}
    const summary = row.summary || {}
    return {
      name: row.name,
      prompt_label: `${config.prompt_template || '-'} / v${config.prompt_version || '-'}`,
      top_k: config.top_k ?? '-',
      citation_accuracy_text: formatRate(summary.citation_accuracy),
      hit_at_k_text: formatRate(summary.hit_at_k),
      refusal_accuracy_text: formatRate(summary.refusal_accuracy),
      badcase_count: row.badcase_count || 0,
      regression_metrics: row.regression_metrics || [],
      config_drift_count: (row.config_drift || []).length,
    }
  })
})
const rolloutRows = computed(() => {
  const rows = rolloutSummary.value.items || []
  return rows
    .filter((row) => row.rollout)
    .map((row) => ({
      name: row.name,
      stable_version: row.active_version_number ? `v${row.active_version_number}` : '-',
      rollout_version: row.rollout?.version_number ? `v${row.rollout.version_number}` : '-',
      percentage_text: row.rollout?.percentage ? `${row.rollout.percentage}%` : '-',
      started_at: row.rollout?.started_at || '-',
    }))
})
const promptTrafficRows = computed(() => {
  const rows = experimentOverview.value.prompt_traffic?.items || []
  return rows.map((row) => ({
    ...row,
    success_rate_text: formatRate(row.calls ? ((row.calls - row.failed_calls) / row.calls) : 0),
  }))
})

// 评测产物状态：等价替代原 el-descriptions（8 项，标签与取值口径不变）
const experimentArtifactRows = computed(() => {
  const artifacts = experimentArtifacts.value
  const summary = experimentSummary.value
  return [
    { key: 'output_dir', label: '输出目录', text: artifacts.output_dir || '-', mono: true },
    { key: 'baseline', label: 'Baseline', text: summary.baseline_experiment || '-' },
    { key: 'bundle', label: 'Bundle', text: summary.bundle_meta?.bundle_name || '默认样例集' },
    { key: 'summary', label: 'Summary 产物', text: artifacts.summary?.exists ? '已生成' : '未生成', tagged: true, ok: Boolean(artifacts.summary?.exists) },
    { key: 'summary_updated_at', label: 'Summary 更新时间', text: artifacts.summary?.updated_at || '-', mono: true },
    { key: 'baseline_snapshot', label: 'Baseline 快照', text: artifacts.baseline_snapshot?.exists ? '已生成' : '未生成', tagged: true, ok: Boolean(artifacts.baseline_snapshot?.exists) },
    { key: 'snapshot_updated_at', label: '快照更新时间', text: artifacts.baseline_snapshot?.updated_at || '-', mono: true },
    { key: 'baseline_citation', label: '当前基线 Citation', text: formatRate(summary.baseline_snapshot?.summary?.citation_accuracy), mono: true },
  ]
})

// 三态（ux-audit M-13）：仅包裹主结果表 experimentRows；多表共用一次 fetch，保留原有 message.error 以覆盖其它表
const experimentStatus = computed(() => {
  if (experimentLoading.value && !experimentRows.value.length) return 'loading'
  if (experimentError.value) return 'error'
  if (!experimentRows.value.length) return 'empty'
  return 'success'
})

const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value) * 100)}%`
}

// 流量窗口切换：实验观测与证据可核验性共用同一个天数，一次刷新两块
const refreshExperimentWindow = () => {
  fetchExperimentOverview()
  fetchEvidenceStats()
  if (issuesVisible.value) loadEvidenceIssues()
}

onMounted(async () => {
  await authStore.loadMe()
  refreshExperimentWindow()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
.experiment-metric-grid {
  margin-top: var(--space-4);
}
/* 评测产物状态是键值读数（含路径与时间戳），用 text-sm 单行不撑破卡片 */
.artifact-grid .app-stat-value {
  font-size: var(--text-sm);
  line-height: var(--text-sm-lh);
  font-weight: 600;
  word-break: break-all;
}
/* 只读横幅不再包在卡片里（避免卡中卡），这里补上与 .app-section-intro 一致的堆叠排版 */
.app-readonly-banner {
  display: grid;
  gap: 6px;
}
/* 证据可核验性：四个读数各带一行口径说明（与 AgentHeader 的 .app-stat-note 同款） */
.evidence-metric-grid {
  margin-top: 0;
}
.evidence-actions {
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-3);
}
/* 引用问题下钻：嵌在证据卡里的二级面板，不另起卡片 */
.evidence-issues {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subtle);
}
.evidence-source-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.evidence-source-title > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.evidence-issues-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.evidence-issues-head .evidence-subhead {
  margin: 0;
}
.evidence-issues-head .el-button {
  margin-left: auto;
}
.issue-ref-list {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
}
.issue-ref-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.issue-ref p {
  margin: 2px 0 0;
  font-size: var(--text-xs);
}
.issue-citation {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.app-stat-note {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
.evidence-level-wrap {
  margin-top: var(--space-4);
}
/* 趋势与法源榜是同一张卡里的二级分区：小标题 + 说明，不另起卡片 */
.evidence-subhead {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin: var(--space-5) 0 var(--space-2);
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
}
.evidence-subhead .app-card-head-meta {
  font-weight: 400;
}
.evidence-empty,
.evidence-note {
  margin: var(--space-2) 0 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.7;
}
/* 令牌里的 .app-metric-grid-4 在 1100px 以下直接收成一列，四个读数用两列更合适 */
@media (max-width: 1100px) {
  .experiment-metric-grid,
  .artifact-grid,
  .evidence-metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
