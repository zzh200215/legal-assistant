import { taskSourceLabel } from './taskSource.js'

const ACTION_LABELS = Object.freeze({
  created: '创建任务',
  status_changed: '状态变更',
  progress_changed: '进度更新',
  comment_added: '添加评论',
  comment_updated: '编辑评论',
  comment_deleted: '删除评论',
  assignee_changed: '负责人变更',
  priority_changed: '优先级变更',
  archived: '归档任务',
  unarchived: '恢复任务',
})

const STATUS_LABELS = Object.freeze({
  todo: '待办',
  in_progress: '进行中',
  done: '已完成',
  cancelled: '已取消',
})

export const taskLogActionLabel = (action) => ACTION_LABELS[action] || action || ''

export const taskLogDetail = (action, detail) => {
  const text = String(detail || '').trim()
  if (!text) return ''
  if (action === 'status_changed') {
    return text
      .replace(/\b(todo|in_progress|done|cancelled)\b/g, (value) => STATUS_LABELS[value])
      .replace(/\s*->\s*/g, ' → ')
      .replace(/\s*\(decomposed\)/, '（拆解触发）')
  }
  if (action === 'progress_changed') {
    const matched = text.match(/^(\d+)\s*->\s*(\d+)$/)
    return matched ? `进度 ${matched[1]}% → ${matched[2]}%` : text
  }
  if (action === 'priority_changed') {
    return text
      .replace(/\b(high|medium|low)\b/g, (value) => ({ high: '高', medium: '中', low: '低' }[value]))
      .replace(/\s*->\s*/g, ' → ')
  }
  if (action === 'created') {
    const matched = text.match(/Task created \(source: ([^)]*)\)/)
    if (!matched) return text
    const source = String(matched[1] || '').trim()
    if (!source || ['none', 'null'].includes(source.toLowerCase())) return '手动创建'
    return `来源：${taskSourceLabel(source)}`
  }
  if (action === 'archived') return '任务已归档'
  if (action === 'unarchived') return '任务已恢复'
  return text
}
