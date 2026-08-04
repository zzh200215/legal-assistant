<template>
  <el-card v-if="versions.length" class="panel-card">
    <template #header>
      <div class="card-header-inline">
        <span>版本记录</span>
        <el-button text @click="$emit('refresh')">刷新</el-button>
      </div>
    </template>
    <div v-if="versions.length" class="stack-list">
      <div v-for="item in versions" :key="`version-${item.id}`" class="stack-item">
        <div class="stack-top">
          <strong>{{ item.title }}</strong>
          <el-tag size="small" type="primary">v{{ item.version_number }}</el-tag>
          <el-tag size="small">{{ item.status }}</el-tag>
        </div>
        <div class="stack-foot">
          <span>{{ item.created_at }}</span>
          <span v-if="item.content_hash">hash {{ item.content_hash.slice(0, 10) }}</span>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { ElCard } from 'element-plus/es/components/card/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/tag/style/css'

defineProps({
  versions: { type: Array, default: () => [] },
})
defineEmits(['refresh'])
</script>
