import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8001'

export default defineConfig({
  cacheDir: 'node_modules/.vite-project-cache',
  plugins: [vue()],
  build: {
    // 目标浏览器按 ES2021 标准构建（Chrome/Edge 85+、Firefox 79+、Safari 15+）。
    // 比 Vite 默认 modules 少一层旧浏览器 downlevel 辅助代码，给 System 分组 chunk 留出余量。
    target: 'es2021',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('axios')) {
            return 'http-vendor'
          }
          const packagePath = id.split('node_modules/')[1]
          if (!packagePath) return
          const segments = packagePath.split('/')
          const packageName = segments[0].startsWith('@')
            ? `${segments[0]}/${segments[1]}`
            : segments[0]
          if (packageName === 'element-plus') {
            return
          }
          if (['vue', 'lodash-unified', '@vue/devtools-api'].includes(packageName)) {
            return
          }
          return `vendor-${packageName.replace('@', '').replace('/', '-')}`
        },
      },
      onwarn(warning, warn) {
        if (warning.code === 'INVALID_ANNOTATION' && warning.id?.includes('@vueuse/core')) {
          return
        }
        warn(warning)
      },
    },
  },
  server: {
    port: 5173,
    // 冷启动预热：e2e 里第一次打开重型路由（Agent / 任务 / 系统 / 文档 / 案件工作台）时，
    // Vite 要现场转换整条模块图，曾多次超过 30s 的用例超时（表现为 page.goto 超时）。
    // 这里在 dev server 起来时就并行预热这些入口，把转换成本移出用例计时。
    warmup: {
      clientFiles: [
        './src/App.vue',
        './src/views/Login.vue',
        './src/views/Agent.vue',
        './src/views/Tasks.vue',
        './src/views/System.vue',
        './src/views/Documents.vue',
        './src/views/LegalWorkspace.vue',
        './src/views/Notifications.vue',
        './src/components/CopilotDock.vue',
      ],
    },
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
