<template>
  <div class="agent-page">
    <AgentHeader />
    <AgentCommandCenter />
    <AgentExecutionView />
    <AgentSidePanels />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import AgentHeader from '../components/agent/AgentHeader.vue'
import AgentCommandCenter from '../components/agent/AgentCommandCenter.vue'
import AgentExecutionView from '../components/agent/AgentExecutionView.vue'
import AgentSidePanels from '../components/agent/AgentSidePanels.vue'
import { useAgentWorkbench } from '../composables/useAgentWorkbench'

const route = useRoute()
const {
  goal, maxSteps, loading,
  initialize, loadRunFromRoute, applyDemoFromRoute,
  syncQueryState, clearPlanPreview, closeAgentWs,
} = useAgentWorkbench()

onMounted(async () => {
  await initialize({
    retryGoal: route.query.retryGoal,
    maxStepsQuery: route.query.maxSteps,
    runId: route.query.runId,
  })
})

onUnmounted(() => {
  closeAgentWs()
})

watch(() => route.query.runId, async (value, oldValue) => {
  if (value === oldValue) return
  await loadRunFromRoute(value)
})

watch(
  () => [route.query.demo, route.query.documentId, route.query.documentTitle],
  () => {
    applyDemoFromRoute()
  }
)

watch([goal, maxSteps], syncQueryState)

watch([goal, maxSteps], () => {
  clearPlanPreview()
})

watch(loading, (value) => {
  if (!value) {
    closeAgentWs()
  }
})
</script>

<style scoped>
.agent-page {
  display: grid;
  gap: var(--space-6);
  max-width: 1600px;
}
</style>
