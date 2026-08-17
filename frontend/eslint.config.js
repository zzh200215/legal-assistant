// ESLint 10 扁平配置（Vue 3 + Prettier）
import js from '@eslint/js'
import globals from 'globals'
import pluginVue from 'eslint-plugin-vue'
import skipFormatting from '@vue/eslint-config-prettier/skip-formatting'

export default [
  {
    name: 'app/ignores',
    ignores: ['dist/**', 'node_modules/**', 'playwright-report/**', 'test-results/**', 'coverage/**'],
  },
  {
    name: 'app/js-base',
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    ...js.configs.recommended,
  },
  ...pluginVue.configs['flat/recommended'],
  {
    name: 'app/vue-overrides',
    files: ['**/*.vue'],
    rules: {
      // 既有单字视图名（Login/Pricing/System/Tasks/Agent/Chat/Documents）为项目约定，
      // 关闭该命名规则（仅 lint 门禁使用；不改动既有组件名以避免无关重构）。
      'vue/multi-word-component-names': 'off',
    },
  },
  skipFormatting,
]
