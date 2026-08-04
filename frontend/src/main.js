import { createApp } from 'vue'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElAside, ElContainer, ElHeader, ElMain } from 'element-plus/es/components/container/index'
import { ElIcon } from 'element-plus/es/components/icon/index'
import { ElLoadingDirective } from 'element-plus/es/components/loading/index'
import { ElMenu, ElMenuItem } from 'element-plus/es/components/menu/index'
import './styles/tailwind.css'
import './styles/design-tokens.css'
import './styles/element-overrides.css'
import 'element-plus/es/components/aside/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/container/style/css'
import 'element-plus/es/components/header/style/css'
import 'element-plus/es/components/icon/style/css'
import 'element-plus/es/components/main/style/css'
import 'element-plus/es/components/menu/style/css'
import 'element-plus/es/components/menu-item/style/css'
import 'element-plus/es/components/loading/style/css'
import 'element-plus/es/components/message/style/css'
import 'element-plus/es/components/message-box/style/css'
import * as Sentry from '@sentry/vue'
import App from './App.vue'
import router from './router'

const app = createApp(App)

const sentryDsn = import.meta.env.VITE_SENTRY_DSN
if (sentryDsn) {
  // 仅启用错误与性能追踪，不启用 replay 屏幕录制（法律数据敏感，不做客户端录制）
  Sentry.init({
    app,
    dsn: sentryDsn,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: 0.1,
    environment: import.meta.env.VITE_APP_ENV || 'development',
  })
}

const isIgnorableRuntimeError = (error) => {
  const message = error?.message || error?.reason?.message || String(error || '')
  return message.includes('ResizeObserver loop completed with undelivered notifications')
    || message.includes('ResizeObserver loop limit exceeded')
}

const showRuntimeError = (error) => {
  if (isIgnorableRuntimeError(error)) {
    const panel = document.getElementById('runtime-error-panel')
    if (panel?.textContent?.includes('ResizeObserver loop')) {
      panel.remove()
    }
    return
  }
  const message = error?.stack || error?.message || String(error || 'Unknown runtime error')
  let panel = document.getElementById('runtime-error-panel')
  if (!panel) {
    panel = document.createElement('pre')
    panel.id = 'runtime-error-panel'
    panel.style.cssText = [
      'position:fixed',
      'inset:16px',
      'z-index:99999',
      'padding:16px',
      'overflow:auto',
      'white-space:pre-wrap',
      'background:#fff1f2',
      'color:#991b1b',
      'border:1px solid #fecdd3',
      'border-radius:8px',
      'font:13px/1.6 Consolas, monospace',
    ].join(';')
    document.body.appendChild(panel)
  }
  panel.textContent = `前端运行时错误：\n\n${message}`
}

app.config.errorHandler = (error) => {
  if (isIgnorableRuntimeError(error)) return
  showRuntimeError(error)
  console.error(error)
}

window.addEventListener('error', (event) => {
  if (isIgnorableRuntimeError(event.error || event.message)) {
    event.preventDefault()
    return
  }
  showRuntimeError(event.error || event.message)
})

window.addEventListener('unhandledrejection', (event) => {
  if (isIgnorableRuntimeError(event.reason)) {
    event.preventDefault()
    return
  }
  showRuntimeError(event.reason)
})

;[
  ElAside,
  ElButton,
  ElContainer,
  ElHeader,
  ElIcon,
  ElMain,
  ElMenu,
  ElMenuItem,
].forEach((component) => {
  app.component(component.name, component)
})

app.directive('loading', ElLoadingDirective)
app.use(router)
router.onError(showRuntimeError)
app.mount('#app')
