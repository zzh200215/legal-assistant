#!/usr/bin/env node
// Bundle 预算检查：读取 dist 产物，校验入口 JS / 单页面 chunk / 总量预算。
// 用法：
//   node scripts/check-bundle-budget.mjs          # 校验（超预算 exit 1）
//   node scripts/check-bundle-budget.mjs --report # 打印当前尺寸
// 预算可经环境变量覆盖（ENTRY_JS_RAW_KB / ENTRY_JS_GZIP_KB / PAGE_CHUNK_RAW_KB / TOTAL_RAW_KB）。
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = resolve(ROOT, 'dist')
const ASSETS = resolve(DIST, 'assets')

// 预算基线：以本次工程化改造前实测产物为基准（入口 98.5kB raw / 34.4kB gzip，最大页面 chunk 120.4kB，
// 总资产 1347kB raw），预算取基线 + 合理余量；改造后 LegalWorkspace/System 拆分应显著低于预算。
const BUDGETS = {
  entryJsRawKB: Number(process.env.ENTRY_JS_RAW_KB || 110),
  entryJsGzipKB: Number(process.env.ENTRY_JS_GZIP_KB || 42),
  pageChunkRawKB: Number(process.env.PAGE_CHUNK_RAW_KB || 130),
  totalRawKB: Number(process.env.TOTAL_RAW_KB || 1450),
}

// 路由级页面 chunk 命名（router.js 懒加载视图）
const PAGE_CHUNK_PATTERN = /^(LegalWorkspace|System|Documents|Agent|Tasks|Chat|Login|LegalPortal|LegalDeveloper|LegalOnboarding|Pricing)-/

function kb(bytes) {
  return bytes / 1024
}

function findEntryJs() {
  // 从 dist/index.html 中读取入口脚本引用（<script type="module" src="/assets/xxx.js">）
  const html = readFileSync(resolve(DIST, 'index.html'), 'utf-8')
  const match = html.match(/<script type="module"[^>]*src="([^"]+\.js)"/)
  if (!match) return null
  const name = match[1].split('/').pop()
  const file = resolve(ASSETS, name)
  return existsSync(file) ? file : null
}

function main() {
  const reportOnly = process.argv.includes('--report')
  if (!existsSync(ASSETS)) {
    console.error('[bundle] 未找到 dist/assets，请先执行 npm run build')
    process.exit(1)
  }
  const files = readdirSync(ASSETS)
    .filter((f) => /\.(js|css)$/.test(f))
    .map((f) => {
      const buf = readFileSync(resolve(ASSETS, f))
      return { name: f, rawKB: kb(buf.length), gzipKB: kb(gzipSync(buf).length) }
    })

  const entryFile = findEntryJs()
  const entry = entryFile ? files.find((f) => f.name === entryFile.split(/[\\/]/).pop()) : null
  const pageChunks = files.filter((f) => f.name.endsWith('.js') && PAGE_CHUNK_PATTERN.test(f.name))
  const largestPageChunk = pageChunks.sort((a, b) => b.rawKB - a.rawKB)[0] || null
  const totalRawKB = files.reduce((sum, f) => sum + f.rawKB, 0)

  const results = []
  if (entry) {
    results.push({ label: '入口 JS (raw)', value: entry.rawKB, limit: BUDGETS.entryJsRawKB, unit: 'KB' })
    results.push({ label: '入口 JS (gzip)', value: entry.gzipKB, limit: BUDGETS.entryJsGzipKB, unit: 'KB' })
  }
  if (largestPageChunk) {
    results.push({ label: `最大页面 chunk (${largestPageChunk.name})`, value: largestPageChunk.rawKB, limit: BUDGETS.pageChunkRawKB, unit: 'KB' })
  }
  results.push({ label: '总资产 (raw)', value: totalRawKB, limit: BUDGETS.totalRawKB, unit: 'KB' })

  let failed = false
  for (const r of results) {
    const flag = r.value > r.limit ? '❌ 超预算' : '✅'
    console.log(`${flag} ${r.label}: ${r.value.toFixed(1)} ${r.unit} (预算 ${r.limit} ${r.unit})`)
    if (r.value > r.limit) failed = true
  }
  if (reportOnly) process.exit(0)
  if (failed) {
    console.error('[bundle] 超出 bundle 预算：请按页面/入口拆分或按需加载后复检')
    process.exit(1)
  }
  console.log('[bundle] 全部预算通过')
}

main()
