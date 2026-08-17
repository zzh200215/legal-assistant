<template>
  <el-dialog
    :model-value="visible"
    title="内容已更新"
    width="480px"
    append-to-body
    :close-on-click-modal="false"
    @update:model-value="$emit('update:visible', $event)"
  >
    <div class="conflict-body">
      <p>你要修改的内容已被其他用户更新，为避免静默覆盖他人修改，本次操作已停止。</p>
      <ul class="conflict-actions">
        <li><strong>刷新内容</strong>：加载最新版本后重新编辑。</li>
        <li><strong>放弃修改</strong>：保留当前页面，不提交本次变更。</li>
        <li><strong>强制覆盖</strong>：仍以你的版本提交（会丢弃他人修改，谨慎使用）。</li>
      </ul>
    </div>
    <template #footer>
      <el-button @click="$emit('discard')">放弃修改</el-button>
      <el-button @click="$emit('refresh')">刷新内容</el-button>
      <el-button type="danger" plain :loading="overriding" @click="$emit('override')">强制覆盖</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'

defineProps({
  visible: { type: Boolean, default: false },
  overriding: { type: Boolean, default: false },
})
defineEmits(['update:visible', 'refresh', 'discard', 'override'])
</script>

<style scoped>
.conflict-body p {
  margin: 0 0 var(--space-3);
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.7;
}
.conflict-actions {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: var(--space-2);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.6;
}
</style>
