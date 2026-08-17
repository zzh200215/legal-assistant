<template>
  <template v-if="!isAdmin">
    <el-card class="system-panel-card">
      <div class="app-readonly-banner">
        <strong>仅管理员可见</strong>
        <span>实验观测面板包含 Prompt 灰度与评测数据，当前账号无权限查看。</span>
      </div>
    </el-card>
  </template>
  <template v-else>
    <div class="app-section-intro tab-intro">
      <strong>实验与灰度观测</strong>
      <span>评估 Prompt 版本效果、流量覆盖情况和基线回退风险。</span>
    </div>

    <el-card class="system-panel-card">
      <div class="app-toolbar">
        <span>流量窗口：</span>
        <el-radio-group v-model="experimentDays" @change="fetchExperimentOverview">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <div class="app-toolbar-right">
          <el-button :loading="experimentLoading" @click="fetchExperimentOverview">刷新</el-button>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="评测样本数" :value="experimentSummary.dataset_size || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="实验数量" :value="experimentSummary.experiment_count || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="回退风险实验" :value="experimentSummary.degraded_experiment_count || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="灰度中的 Prompt" :value="rolloutSummary.active_rollout_count || 0" />
        </el-card>
      </el-col>
    </el-row>

    <el-card style="margin-bottom: 16px">
      <template #header>评测产物状态</template>
      <el-descriptions :column="4" border>
        <el-descriptions-item label="输出目录">{{ experimentArtifacts.output_dir || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Baseline">{{ experimentSummary.baseline_experiment || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Bundle">{{ experimentSummary.bundle_meta?.bundle_name || '默认样例集' }}</el-descriptions-item>
        <el-descriptions-item label="Summary 产物">{{ experimentArtifacts.summary?.exists ? '已生成' : '未生成' }}</el-descriptions-item>
        <el-descriptions-item label="Summary 更新时间">{{ experimentArtifacts.summary?.updated_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Baseline 快照">{{ experimentArtifacts.baseline_snapshot?.exists ? '已生成' : '未生成' }}</el-descriptions-item>
        <el-descriptions-item label="快照更新时间">{{ experimentArtifacts.baseline_snapshot?.updated_at || '-' }}</el-descriptions-item>
        <el-descriptions-item label="当前基线 Citation">{{ formatRate(experimentSummary.baseline_snapshot?.summary?.citation_accuracy) }}</el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-row :gutter="16" style="margin-bottom: 16px">
      <el-col :span="15">
        <el-card>
          <template #header>实验结果</template>
          <el-table :data="experimentRows" v-loading="experimentLoading" border size="small" max-height="420">
            <el-table-column prop="name" label="实验" width="150" show-overflow-tooltip />
            <el-table-column prop="prompt_label" label="Prompt" width="140" show-overflow-tooltip />
            <el-table-column prop="top_k" label="TopK" width="70" />
            <el-table-column prop="citation_accuracy_text" label="Citation" width="100" />
            <el-table-column prop="hit_at_k_text" label="Hit@K" width="90" />
            <el-table-column prop="refusal_accuracy_text" label="Refusal" width="90" />
            <el-table-column prop="badcase_count" label="Badcase" width="90" />
            <el-table-column label="对基线">
              <template #default="{ row }">
                <el-tag v-if="row.regression_metrics?.length" type="danger" size="small">
                  {{ row.regression_metrics.join(', ') }}
                </el-tag>
                <span v-else style="color: var(--color-success)">无回退</span>
              </template>
            </el-table-column>
            <el-table-column label="配置漂移" width="90">
              <template #default="{ row }">{{ row.config_drift_count }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="9">
        <el-card>
          <template #header>灰度发布</template>
          <el-table :data="rolloutRows" v-loading="experimentLoading" border size="small" max-height="420">
            <el-table-column prop="name" label="模板" min-width="150" show-overflow-tooltip />
            <el-table-column prop="stable_version" label="稳定版" width="80" />
            <el-table-column prop="rollout_version" label="灰度版" width="80" />
            <el-table-column prop="percentage_text" label="比例" width="80" />
            <el-table-column prop="started_at" label="开始时间" width="180" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <template #header>Prompt 实际流量覆盖</template>
      <el-table :data="promptTrafficRows" v-loading="experimentLoading" border size="small" max-height="420">
        <el-table-column prop="prompt_template" label="Prompt" min-width="160" show-overflow-tooltip />
        <el-table-column prop="prompt_version" label="版本" width="80">
          <template #default="{ row }">v{{ row.prompt_version }}</template>
        </el-table-column>
        <el-table-column prop="calls" label="调用数" width="90" />
        <el-table-column prop="failed_calls" label="失败数" width="90" />
        <el-table-column prop="success_rate_text" label="成功率" width="90" />
        <el-table-column prop="last_called_at" label="最近命中" width="180" />
      </el-table>
    </el-card>
  </template>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../../api'
import { useSystemObservability } from '../../composables/useSystemObservability'
import { useAuthStore } from '../../stores/auth'

const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const {
  experimentDays,
  experimentLoading,
  experimentOverview,
  fetchExperimentOverview,
} = useSystemObservability({ client: api, message: ElMessage, isAdmin })

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

const formatRate = (value) => {
  if (value === null || value === undefined || value === '') return '-'
  return `${Math.round(Number(value) * 100)}%`
}

onMounted(async () => {
  await authStore.loadMe()
  fetchExperimentOverview()
})
</script>

<style scoped>
.tab-intro {
  margin-top: var(--space-5);
}
.system-panel-card {
  margin-top: var(--space-4);
}
</style>
