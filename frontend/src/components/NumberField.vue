<template>
  <el-input
    :model-value="text"
    :placeholder="placeholder"
    :disabled="disabled"
    :size="size || undefined"
    inputmode="decimal"
    class="number-field"
    @update:model-value="onInput"
    @blur="commit"
    @keydown.enter.prevent="commit"
  />
</template>

<script setup>
// 数字输入：替代 el-input-number（省 7.6 KB），保留 min/max/precision 语义。
// 输入过程只回传能解析的数值，失焦/回车时再夹取范围并补齐小数位，
// 避免用户输入「1440」时在中间态被截成 144。
import { ref, watch } from 'vue'
import { ElInput } from 'element-plus/es/components/input/index'
import 'element-plus/es/components/input/style/css'
import { clampNumber, formatNumberInput, parseNumberInput } from '../utils/numberField'

const props = defineProps({
  modelValue: { type: Number, default: null },
  min: { type: Number, default: null },
  max: { type: Number, default: null },
  precision: { type: Number, default: 0 },
  placeholder: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  size: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const text = ref(formatNumberInput(props.modelValue, props.precision))

watch(
  () => [props.modelValue, props.precision],
  () => {
    const next = formatNumberInput(props.modelValue, props.precision)
    if (next !== text.value) text.value = next
  },
)

const onInput = (value) => {
  text.value = String(value ?? '')
  const parsed = parseNumberInput(text.value)
  if (parsed !== null) emit('update:modelValue', parsed)
}

const commit = () => {
  const normalized = clampNumber(text.value, {
    min: props.min,
    max: props.max,
    precision: props.precision,
  })
  text.value = formatNumberInput(normalized, props.precision)
  if (normalized !== props.modelValue) emit('update:modelValue', normalized)
}
</script>
