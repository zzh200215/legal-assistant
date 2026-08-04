const STATUS_META = {
  async: {
    PENDING: { label: '排队中', type: 'info' },
    STARTED: { label: '已开始', type: 'warning' },
    PROCESSING: { label: '处理中', type: 'warning' },
    RETRY: { label: '重试中', type: 'warning' },
    SUCCESS: { label: '已完成', type: 'success' },
    FAILURE: { label: '失败', type: 'danger' },
  },
  agent: {
    completed: { label: '已完成', type: 'success' },
    running: { label: '执行中', type: 'warning' },
    error: { label: '失败', type: 'danger' },
  },
  task: {
    todo: { label: '待办', type: 'info' },
    in_progress: { label: '进行中', type: 'primary' },
    done: { label: '已完成', type: 'success' },
    cancelled: { label: '已取消', type: 'danger' },
  },
  task_run: {
    pending: { label: '排队中', type: 'info' },
    running: { label: '执行中', type: 'warning' },
    succeeded: { label: '已完成', type: 'success' },
    failed: { label: '失败', type: 'danger' },
  },
}

export function getStatusMeta(kind, status) {
  const normalizedKind = kind || 'async'
  const statusMap = STATUS_META[normalizedKind] || {}
  return statusMap[status] || {
    label: status || '未知',
    type: 'info',
  }
}

export function getStatusLabel(kind, status) {
  return getStatusMeta(kind, status).label
}

export function getStatusTagType(kind, status) {
  return getStatusMeta(kind, status).type
}
