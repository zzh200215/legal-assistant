<template>
  <button
    type="button"
    role="switch"
    class="app-switch"
    :class="{ 'is-on': modelValue }"
    :aria-checked="modelValue ? 'true' : 'false'"
    :aria-busy="loading ? 'true' : 'false'"
    :disabled="disabled || loading"
    @click="toggle"
  >
    <span class="app-switch-knob" />
  </button>
</template>

<script setup>
// 开关：替代 el-switch（省 5.1 KB），保留 v-model 与 change 两种用法。
// loading 只做禁用 + aria-busy，不再显示旋转图标（调用方通常已有「保存中」文案）。
const props = defineProps({
  modelValue: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'change'])

const toggle = () => {
  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('change', next)
}
</script>

<style scoped>
.app-switch {
  position: relative;
  flex: 0 0 auto;
  width: 36px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-subtle);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
}
.app-switch.is-on { border-color: var(--color-primary); background: var(--color-primary); }
.app-switch:disabled { opacity: 0.6; cursor: not-allowed; }
.app-switch:focus-visible { outline: 2px solid var(--color-primary-subtle); outline-offset: 2px; }
.app-switch-knob {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-surface);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.25);
  transition: transform var(--transition-fast);
}
.app-switch.is-on .app-switch-knob { transform: translateX(16px); }
</style>
