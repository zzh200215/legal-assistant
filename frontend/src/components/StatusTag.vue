<template>
  <el-tag :type="tagType" :size="size">
    {{ label }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'
import { ElTag } from 'element-plus/es/components/tag/index'
import { getStatusLabel, getStatusTagType } from '../utils/status'

const props = defineProps({
  kind: {
    type: String,
    default: 'async',
  },
  status: {
    type: String,
    default: '',
  },
  size: {
    type: String,
    default: 'default',
  },
})

const label = computed(() => getStatusLabel(props.kind, props.status))
const tagType = computed(() => getStatusTagType(props.kind, props.status))
</script>
