<template>
  <section class="cop-trace">
    <div class="cop-trace-head">
      <h3 class="cop-section-title">处理过程</h3>
      <!-- 口径标签常显：有服务端 trace 标「服务端步骤」，没有则标「事后回放」，两种口径都不含糊 -->
      <span class="cop-mode" :title="trace.caption">{{ trace.modeLabel }}</span>
    </div>
    <p class="cop-note">{{ trace.caption }}</p>
    <ol class="cop-steps">
      <li
        v-for="step in trace.steps"
        :key="step.id"
        class="cop-step"
        :class="step.status ? `is-${step.status}` : ''"
      >
        <span class="cop-step-text">
          <span class="cop-step-line">
            <b>{{ step.label }}</b>
            <span v-if="step.statusLabel" class="cop-step-status">{{ step.statusLabel }}</span>
            <span v-if="step.duration" class="cop-step-ms">{{ step.duration }}</span>
          </span>
          <span>{{ step.detail }}</span>
        </span>
      </li>
    </ol>
  </section>
</template>

<script setup>
// 过程时间线：props.trace 由 composables/copilotLayers.js 的 deriveTraceReplay 生成。
// 两种口径：① 服务端 trace（真实步骤 + 状态 + 耗时，标「服务端步骤」）；
// ② 响应字段事后回放（老记录 / 追问链路没有 trace，标「事后回放」，每条 detail 都能找到字段出处）。
// 这里只负责展示，不做任何补充推断。
defineProps({ trace: { type: Object, required: true } })
</script>

<style scoped>
/* 版式基准：预览的 .timeline —— 左侧竖线 + 空心圆点，步骤之间靠连线串起来。
   全部步骤都是「已回放」的既定事实，故圆点统一用青绿描边，不做成功/失败区分。 */
.cop-trace {
  display: grid;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-4) 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface-subtle);
}

.cop-trace-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.cop-section-title {
  margin: 0;
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 700;
}

/* 口径标签：中性浅底即可，不抢注意力，也不暗示成功 */
.cop-mode {
  margin-left: auto;
  padding: 2px 9px;
  border-radius: var(--radius-full);
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  font-weight: 600;
  white-space: nowrap;
}

.cop-steps {
  display: grid;
  gap: 0;
  margin: var(--space-1) 0 0;
  padding: 0;
  list-style: none;
}

.cop-step {
  position: relative;
  padding: 0 0 12px 22px;
}

.cop-step:last-child {
  padding-bottom: 0;
}

/* 竖线：连接相邻节点；最后一个节点不再往下画 */
.cop-step::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 16px;
  bottom: 0;
  width: 2px;
  background: var(--color-border);
}

.cop-step:last-child::before {
  display: none;
}

/* 圆点：中性面上的空心青绿圆，与「进行中」的品牌色光圈同族 */
.cop-step::after {
  content: '';
  position: absolute;
  left: 0;
  top: 3px;
  width: 12px;
  height: 12px;
  border-radius: var(--radius-full);
  background: var(--color-surface);
  border: 2px solid var(--color-accent);
}

.cop-step-text {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.cop-step-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

/* 状态与耗时只在服务端 trace 里出现；事后回放的步骤没有这两个字段，版式与从前一致 */
.cop-step-status,
.cop-step-ms {
  padding: 1px 7px;
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1.6;
}

.cop-step-status {
  background: var(--color-accent-light);
  color: var(--color-accent);
}

.cop-step-ms {
  background: var(--color-bg-alt);
  color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums;
}

/* 圆点按步骤状态换色：成功保持青绿，降级/拦截/跳过各有其色，避免「一律成功」的暗示 */
.cop-step.is-degraded::after {
  border-color: var(--color-warning);
}

.cop-step.is-refused::after {
  border-color: var(--color-danger);
}

.cop-step.is-skipped::after {
  border-color: var(--color-border-hover);
}

.cop-step-text b {
  color: var(--color-text);
  font-size: var(--text-sm);
  font-weight: 600;
}

.cop-step-text span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
  word-break: break-word;
}

/* 序号由 <ol> 语义承载，视觉顺序交给左侧圆点与连线 */

.cop-note {
  margin: 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  line-height: 1.6;
}
</style>
