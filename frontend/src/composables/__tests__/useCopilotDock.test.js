import test from 'node:test'
import assert from 'node:assert/strict'
import { useCopilotDock } from '../useCopilotDock.js'

// 入口收敛后助手面板是唯一入口：业务页面（案件工作台「打开助手」、工作台快捷入口）
// 都通过这个 composable 打开面板，因此它必须是跨组件共享的单例状态。

test('默认展开，且能开/关/切换', () => {
  const { open, openCopilot, closeCopilot, toggleCopilot } = useCopilotDock()
  open.value = true
  closeCopilot()
  assert.equal(open.value, false)
  openCopilot()
  assert.equal(open.value, true)
  toggleCopilot()
  assert.equal(open.value, false)
  open.value = true
})

test('多个调用点共享同一份状态（单例）', () => {
  const first = useCopilotDock()
  const second = useCopilotDock()
  first.closeCopilot()
  assert.equal(second.open.value, false)
  second.openCopilot()
  assert.equal(first.open.value, true)
})
