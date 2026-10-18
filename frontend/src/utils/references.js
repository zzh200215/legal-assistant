/**
 * 法源引用归一。
 *
 * references 由模型或历史数据产出，形状并不稳定：整段可能是 null / 字符串，
 * 数组里也可能混进 null 或空对象（线上真的出现过，见 verify-u2-u4 集成用例）。
 * 这类条目一旦进入渲染循环，`.source_id` / `.title` 取值就会抛错并让整块结果白屏，
 * 所以在渲染前统一过滤成「至少有一个可用字段的对象」。
 */
export function asReferenceList(value) {
  if (!Array.isArray(value)) return []
  return value.filter(
    (item) =>
      item !== null &&
      typeof item === 'object' &&
      !Array.isArray(item) &&
      Boolean(item.title || item.source_id || item.citation),
  )
}

export default { asReferenceList }
