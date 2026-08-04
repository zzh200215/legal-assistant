import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || 'http://localhost:8001'

export default defineConfig({
  cacheDir: 'node_modules/.vite-project-cache',
  plugins: [vue(), tailwindcss()],
  build: {
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
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
