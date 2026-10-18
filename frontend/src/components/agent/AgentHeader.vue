<template>
  <div class="agent-header">
    <!-- 方向 B 页头：标题 + 副标题在左，推荐示例收进右侧动作区；不再用带边框的条状容器包标题 -->
    <header class="app-page-head">
      <div class="app-page-head-main">
        <h1 class="app-page-title">Agent 自动化执行</h1>
        <p class="app-page-sub">一句话目标自动规划、分步执行；敏感动作停下来等人工确认，每次运行都能回看完整链路。</p>
      </div>
      <div class="app-page-head-actions example-actions">
        <span class="example-label">推荐示例</span>
        <el-button size="small" plain @click="applyExample('总结文档 1，并提取其中的风险点')">文档风险</el-button>
        <el-button size="small" plain @click="applyExample('审查这份合同并提示风险条款')">合同审查</el-button>
        <el-button size="small" plain @click="applyExample('生成一份劳动争议仲裁申请书草稿')">文书草稿</el-button>
      </div>
    </header>

    <!-- 概览：三个读数用共享 .app-stat 数据卡；.app-metric-grid-4 是四列骨架，本页只有三项，收成三列 -->
    <section class="app-metric-grid-4 agent-overview" aria-label="Agent 概览">
      <div class="app-stat">
        <span class="app-stat-label">待审批动作</span>
        <div class="app-stat-value">
          {{ approvals.length }}
          <span v-if="approvals.length" class="app-tag is-pending">需确认</span>
          <small v-else>暂无</small>
        </div>
        <span class="app-stat-note">高风险工具调用会在这里进入人工确认。</span>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">运行历史</span>
        <div class="app-stat-value">
          {{ historyTotal }}
          <small>次运行</small>
        </div>
        <span class="app-stat-note">可回看执行目标、最终结果和完整步骤链路。</span>
      </div>
      <div class="app-stat">
        <span class="app-stat-label">当前模式</span>
        <div class="app-stat-value mode-value">{{ demoPreset ? '演示链路' : '自由执行' }}</div>
        <span class="app-stat-note">{{ demoPreset ? demoPreset.title : '按一句话目标自动规划并执行。' }}</span>
      </div>
    </section>

    <!-- 专家网络：标题独立成行，角色卡直接用 .app-card 网格，避免卡中卡 -->
    <section class="expert-directory" aria-label="专家角色目录">
      <div class="directory-heading">
        <h2 class="directory-title">企业专家协作网络</h2>
        <p class="directory-note">总管负责编排，专家负责结论，执行层只处理已确认动作。</p>
      </div>
      <div class="expert-role-grid">
        <article
          v-for="role in expertRoles"
          :key="role.agent_type"
          class="app-card expert-role"
        >
          <div class="app-card-body">
            <div class="expert-role-topline">
              <strong>{{ role.label }}</strong>
              <span class="app-tag" :class="executionModeTone(role.execution_mode)">
                {{ executionModeLabel(role.execution_mode) }}
              </span>
            </div>
            <p class="expert-role-desc">{{ role.description }}</p>
            <div class="expert-role-contract">
              <span class="contract-label">交付</span>
              <strong>{{ role.output_contract }}</strong>
            </div>
            <div class="expert-role-tools">
              <span v-for="tool in role.allowed_tools || []" :key="`${role.agent_type}-${tool}`" class="app-tag">
                {{ toolLabel(tool) }}
              </span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- 执行口径：原来手写的彩色小胶囊换成 .app-card 外壳里的 .app-tag chips -->
    <section class="app-card command-card">
      <div class="app-card-head">
        <h2>执行口径</h2>
        <span class="app-card-head-meta">低风险步骤自动执行，敏感动作按需确认</span>
      </div>
      <div class="app-card-body">
        <div class="command-chips">
          <span class="app-tag">最大步数 {{ maxSteps }}</span>
          <span class="app-tag">审批 {{ approvals.length }}</span>
          <span class="app-tag">历史 {{ historyTotal }}</span>
          <span class="app-tag">成功率 {{ percent(agentMetrics.success_rate) }}</span>
          <span class="app-tag">工具成功 {{ percent(agentMetrics.reliability?.tool_success_rate) }}</span>
          <span class="app-tag">重试任务 {{ percent(agentMetrics.reliability?.retrying_run_rate) }}</span>
          <span class="app-tag">人工介入 {{ percent(agentMetrics.reliability?.human_intervention_rate) }}</span>
        </div>
        <p class="command-note">适合处理文档风险、合同审查、法律咨询和跨模块串联动作。</p>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/button/style/css'
import { executionModeLabel, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  applyExample, approvals, historyTotal, demoPreset, maxSteps, agentMetrics, expertRoles,
} = useAgentWorkbench()

/** 专家执行模式对应的 .app-tag 变体：受控执行要确认，编排类只做规划，其余为只读/安全模式 */
function executionModeTone(mode) {
  if (mode === 'controlled_side_effect') return 'is-pending'
  if (mode === 'orchestration_only') return 'is-info'
  return 'is-ok'
}

/** 指标读数：后端未返回时显示 '-'，与改造前口径一致 */
function percent(value) {
  return value == null ? '-' : `${Math.round(value * 100)}%`
}
</script>

<style scoped>
.agent-header {
  display: grid;
  gap: var(--space-6);
}

.example-actions {
  gap: var(--space-2);
}

.example-label {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  font-weight: 500;
}

/* .app-metric-grid-4 默认四列，本页概览只有三个读数：覆盖成三列，窄屏退两列再退一列 */
.agent-overview {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.app-stat-note {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

/* “当前模式”是文字读数，字号收一档，避免和数字读数抢层级 */
.app-stat-value.mode-value {
  font-size: var(--text-xl);
  font-weight: 600;
}

.expert-directory {
  display: grid;
  gap: var(--space-4);
}

.directory-heading {
  display: grid;
  gap: 4px;
}

.directory-title {
  margin: 0;
  color: var(--color-text);
  font-size: var(--text-lg);
  font-weight: 600;
}

.directory-note {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}

.expert-role-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

/* 卡片本体由 .app-card 提供，这里让正文撑满卡片高度，四列底部对齐 */
.expert-role .app-card-body {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  height: 100%;
}

.expert-role-topline {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  align-items: center;
}

.expert-role-topline strong {
  color: var(--color-text);
  font-size: var(--text-sm);
}

.expert-role-desc {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.65;
}

.expert-role-contract {
  display: grid;
  gap: 2px;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}

.contract-label {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.expert-role-contract strong {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1.55;
}

.expert-role-tools {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.command-card .app-card-body {
  display: grid;
  gap: var(--space-3);
}

.command-chips {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.command-note {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .agent-overview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .expert-role-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .agent-overview,
  .expert-role-grid {
    grid-template-columns: 1fr;
  }

  .expert-role .app-card-body {
    height: auto;
  }
}
</style>
