import test from 'node:test'
import assert from 'node:assert/strict'
import { capabilitiesForRole, CAPABILITY } from '../capabilities.js'

test('admin 拥有系统/Agent/任务权限', () => {
  const caps = capabilitiesForRole('admin')
  assert.ok(caps.includes(CAPABILITY.SYSTEM_VIEW))
  assert.ok(caps.includes(CAPABILITY.AGENT_VIEW))
  assert.ok(caps.includes(CAPABILITY.TASK_VIEW))
  assert.ok(caps.includes(CAPABILITY.DOCUMENT_READ))
  assert.ok(caps.includes(CAPABILITY.WORKSPACE_MANAGE))
})

test('user 可查看任务但无管理员权限', () => {
  const caps = capabilitiesForRole('user')
  assert.ok(caps.includes(CAPABILITY.DOCUMENT_READ))
  assert.ok(caps.includes(CAPABILITY.WORKSPACE_MANAGE))
  assert.ok(caps.includes(CAPABILITY.TASK_VIEW))
  assert.ok(!caps.includes(CAPABILITY.SYSTEM_VIEW))
  assert.ok(!caps.includes(CAPABILITY.AGENT_VIEW))
})

test('dept_admin 介于两者之间', () => {
  const caps = capabilitiesForRole('dept_admin')
  assert.ok(caps.includes(CAPABILITY.WORKSPACE_REVIEW))
  assert.ok(caps.includes(CAPABILITY.TASK_VIEW))
  assert.ok(!caps.includes(CAPABILITY.SYSTEM_VIEW))
})

test('未知角色默认不放行（空能力列表）', () => {
  const caps = capabilitiesForRole('superuser')
  assert.deepEqual(caps, [])
})
