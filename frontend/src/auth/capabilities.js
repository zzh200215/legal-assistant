// Capability 权限字典（前端单一契约来源）。
// 稳定机器标识（如 document.read），禁止在页面散落 role 字符串判断。
// 前端权限仅用于 UX 控制（隐藏/禁用/403 状态），不是安全边界——所有后端接口仍做服务端校验。
// 来源：后端 UserRole 枚举（user / dept_admin / admin）。
// 依赖（后端任务）：/auth/me 或独立端点返回 capabilities 后，前端删除本映射、
// 直接读取后端下发的能力列表（见 docs/CONFIG.md「Capability 共享契约」）。

export const CAPABILITY = {
  DOCUMENT_READ: 'document.read',
  DOCUMENT_UPLOAD: 'document.upload',
  DOCUMENT_ASK: 'document.ask',
  DOCUMENT_ANALYZE: 'document.analyze',
  DOCUMENT_EXPORT: 'document.export',
  WORKSPACE_MANAGE: 'workspace.manage',
  WORKSPACE_CONSULT: 'workspace.consult',
  WORKSPACE_REVIEW: 'workspace.review',
  AGENT_VIEW: 'agent.view',
  AGENT_RUN: 'agent.run',
  AGENT_CANCEL: 'agent.cancel',
  SYSTEM_VIEW: 'system.view',
  SYSTEM_MANAGE: 'system.manage',
  TASK_VIEW: 'task.view',
  ORG_MANAGE: 'org.manage',
}

const USER_CAPS = [
  CAPABILITY.DOCUMENT_READ,
  CAPABILITY.DOCUMENT_UPLOAD,
  CAPABILITY.DOCUMENT_ASK,
  CAPABILITY.DOCUMENT_ANALYZE,
  CAPABILITY.WORKSPACE_MANAGE,
  CAPABILITY.WORKSPACE_CONSULT,
  // 任务中心对全部登录用户开放（后端 /tasks/* 为 get_current_user 权限），
  // 导航入口按产品规则仅管理员显示，但直接路由访问不被前端拦截
  CAPABILITY.TASK_VIEW,
]

const DEPT_ADMIN_CAPS = [
  ...USER_CAPS,
  CAPABILITY.DOCUMENT_EXPORT,
  CAPABILITY.WORKSPACE_REVIEW,
]

const ADMIN_CAPS = [
  ...DEPT_ADMIN_CAPS,
  CAPABILITY.AGENT_VIEW,
  CAPABILITY.AGENT_RUN,
  CAPABILITY.AGENT_CANCEL,
  CAPABILITY.SYSTEM_VIEW,
  CAPABILITY.SYSTEM_MANAGE,
  CAPABILITY.TASK_VIEW,
  CAPABILITY.ORG_MANAGE,
]

const ROLE_CAPABILITIES = {
  user: USER_CAPS,
  dept_admin: DEPT_ADMIN_CAPS,
  admin: ADMIN_CAPS,
}

/** 角色 → 能力列表；未知角色返回空数组（权限未知默认不放行） */
export function capabilitiesForRole(role) {
  return ROLE_CAPABILITIES[role] || []
}
