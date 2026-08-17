#!/usr/bin/env node
// 从 docs/openapi-snapshot.json 生成前端 JSDoc 类型声明（src/types/api.gen.js）。
// 用法：
//   node scripts/gen-api-types.mjs           # 生成（覆盖）
//   node scripts/gen-api-types.mjs --check   # 新鲜度检查（CI：与已提交文件不一致时 exit 1）
// 生成文件禁止手工编辑；后端 OpenAPI 变更需先更新快照（scripts/export_openapi.py --update）再重生成。
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SNAPSHOT = resolve(ROOT, '../docs/openapi-snapshot.json')
const OUT = resolve(ROOT, 'src/types/api.gen.js')

/** 解析 JSON Schema → JSDoc 类型字符串；内联对象返回 'Object' */
function jsDocType(schema, schemas) {
  if (!schema || typeof schema !== 'object') return '*'
  if (schema.$ref) {
    const name = schema.$ref.split('/').pop()
    return schemas[name] ? name : '*'
  }
  if (schema.enum) {
    const values = schema.enum
      .map((v) => (typeof v === 'string' ? `'${v.replace(/'/g, "\\'")}'` : String(v)))
      .join(' | ')
    return values || 'string'
  }
  if (schema.oneOf || schema.anyOf) {
    const list = schema.oneOf || schema.anyOf
    return list.map((s) => jsDocType(s, schemas)).filter((t) => t !== '*').join(' | ') || '*'
  }
  if (schema.allOf) {
    return schema.allOf.map((s) => jsDocType(s, schemas)).filter((t) => t !== '*').join(' & ') || '*'
  }
  switch (schema.type) {
    case 'string': return 'string'
    case 'integer': case 'number': return 'number'
    case 'boolean': return 'boolean'
    case 'null': return 'null'
    case 'array': return `Array<${jsDocType(schema.items, schemas)}>`
    case 'object': return 'Object'
    default: return '*'
  }
}

function render() {
  const doc = JSON.parse(readFileSync(SNAPSHOT, 'utf-8'))
  const schemas = doc.components?.schemas || {}
  const names = Object.keys(schemas).sort()

  const lines = [
    '本文件由 scripts/gen-api-types.mjs 自动生成，禁止手工编辑。',
    '数据源：docs/openapi-snapshot.json（后端 OpenAPI 快照）。',
    '用途：为 JS 业务代码提供请求/响应结构契约（JSDoc @typedef），',
    '      页面 view model 应基于生成类型派生，不要反改生成文件。',
  ]
  for (const name of names) {
    const schema = schemas[name]
    const props = schema.properties
    if (schema.type === 'object' && props && Object.keys(props).length) {
      lines.push(`@typedef {Object} ${name}`)
      for (const key of Object.keys(props).sort()) {
        const required = Array.isArray(schema.required) && schema.required.includes(key)
        lines.push(`@property {${jsDocType(props[key], schemas)}}${required ? '' : '='} ${key}`)
      }
    } else {
      lines.push(`@typedef {${jsDocType(schema, schemas)}} ${name}`)
    }
  }
  return `/**\n${lines.map((l) => ` * ${l}`).join('\n')}\n */\n`
}

function main() {
  const check = process.argv.includes('--check')
  const content = render()
  if (check) {
    let existing = ''
    try {
      existing = readFileSync(OUT, 'utf-8')
    } catch {
      existing = ''
    }
    if (existing.trim() !== content.trim()) {
      console.error('[types:check] src/types/api.gen.js 与 OpenAPI 快照不一致，请运行 node scripts/gen-api-types.mjs 重新生成后提交')
      process.exit(1)
    }
    console.log('[types:check] OK（生成文件与 OpenAPI 快照一致）')
    process.exit(0)
  }
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, content, 'utf-8')
  console.log(`[types:gen] 已生成 ${OUT}`)
}

main()
