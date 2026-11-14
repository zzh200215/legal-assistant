// 数字输入框的纯函数：解析用户输入、按 min/max/precision 归一。页面与组件共用，
// 便于单测覆盖「空值 / 半截输入 / 越界 / 小数位」这些 el-input-number 曾替我们处理的细节。

// 解析输入文本：空串与非法字符返回 null；半截输入（如 "1."）按已输入部分解析。
export function parseNumberInput(text) {
  if (text === null || text === undefined) return null
  const normalized = String(text).trim().replace(/[，,\s]/g, '')
  if (!normalized) return null
  if (!/^-?\d*\.?\d*$/.test(normalized)) return null
  const value = Number(normalized)
  return Number.isFinite(value) ? value : null
}

// 归一：先按小数位四舍五入，再夹到 [min, max]；没有可解析的值时返回 null。
export function clampNumber(value, { min = null, max = null, precision = 0 } = {}) {
  if (value === null || value === undefined || value === '') return null
  let number = Number(value)
  if (!Number.isFinite(number)) return null
  if (typeof precision === 'number' && precision >= 0) {
    number = Number(number.toFixed(precision))
  }
  if (typeof min === 'number' && number < min) number = min
  if (typeof max === 'number' && number > max) number = max
  return number
}

// 显示值：整数不带小数点，小数按 precision 补齐；空值显示空串。
export function formatNumberInput(value, precision = 0) {
  if (value === null || value === undefined || value === '') return ''
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  return precision > 0 ? number.toFixed(precision) : String(number)
}
