#!/usr/bin/env node
// Bundle 预算检查：读取 dist 产物，校验入口 JS / 单页面 chunk / 总量预算。
// 用法：
//   node scripts/check-bundle-budget.mjs          # 校验（超预算 exit 1）
//   node scripts/check-bundle-budget.mjs --report # 打印当前尺寸
// 预算可经环境变量覆盖（ENTRY_JS_RAW_KB / ENTRY_JS_GZIP_KB / PAGE_CHUNK_RAW_KB / TOTAL_RAW_KB）。
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gzipSync } from 'node:zlib'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const DIST = resolve(ROOT, 'dist')
const ASSETS = resolve(DIST, 'assets')

// 预算基线：以工程化改造前实测产物为基准（入口 98.5kB raw / 34.4kB gzip，最大页面 chunk 120.4kB，
// 总资产 1347kB raw），预算取基线 + 合理余量；改造后 LegalWorkspace/System 拆分应显著低于预算。
// 2026-10 法律工作台重构（案件工作区 + 工作流面板 + 通知中心 + 管理中心）后重定基线：
// 实测入口 110.1kB raw / 37.0kB gzip、最大页面 chunk 102.3kB、总资产 1566.9kB；
// LegalWorkspace 面板已按需加载（defineAsyncComponent），prefill/refresh 时序依赖的 tab 保持静态。
// 2026-10-10 吞错修复（M-13）后 NotificationBell 首次引入 ElMessage（操作反馈），入口 raw 重定 120：
// 实测入口 118.5kB raw / 40.1kB gzip、最大页面 chunk 102.2kB、总资产 1581.4kB。
// 2026-10-18 包体治理（step10）：修掉 32 个文件的 `import { ElMessage } from 'element-plus'` 桶导入、
// 删掉 main.js 里零使用的组件注册、把最后两处 el-row/el-col 换成原生栅格后，
// 实测入口 102.0kB raw / 35.8kB gzip、最大页面 chunk 121.8kB（System）、总资产 1579.0kB——四项全部达标，
// 故本次**不下调也不放宽**预算：现有红线已被证明可达，保留其约束力（详见 docs/review/step10-bundle-budget.md）。
//
// 2026-10-20 余量治理（step20）：总量已经贴线（1599.9 / 1600，只剩 0.1kB），逐个量过所有能做的地方——
// ① CSS 合并（cssCodeSplit: false）：只省 3.2kB，还牺牲按路由加载 CSS，不做；
// ② Sentry：生产构建下 `VITE_SENTRY_DSN` 为空，整个 SDK 已被 tree-shake（dist 里搜不到 browserTracing）；
// ③ 运行时依赖：逐个扫描全部有引用，没有未用大包；
// ④ 图标：`@element-plus/icons-vue` 只有 Chat 显式用一枚，其余是 element-plus 组件内部在用，删不掉；
// ⑤ 死 CSS：删掉 el-menu / el-statistic 的覆写（组件已不再使用），design-tokens 里另有约 18 个
//    direction B 之前的遗留类与在用选择器交织，收益 ~1.4kB、风险高于收益，留作开口项。
// 真正有效的一招是**把 copilot 面板改成按需加载**：入口 114.8 → 77.0kB raw、41.1 → 26.9kB gzip；
// 但代码只是搬进异步 chunk，总量基本不动（仅分块开销 +2.4kB 后又被死 CSS 抵消一部分）。
// 所以本轮**重定预算**：入口按新实测收紧（100 / 35），总量按实测 +6% 放宽到 1700，页面 chunk 不动；
// 另加 95% 预警档，让下一次增长先出 ⚠️ 而不是直接撞红。
// 2026-10-28 包体治理（step53/54）：System 页只保留健康检查与概览条，运营/表格与观测/图表
// 两组 tab 改为异步 chunk（SystemOpsTabs / SystemAnalyticsTabs）；LegalWorkspace 的合同审查/
// 文书/审核三个主 tab 也改为独立异步 chunk。构建目标从 Vite 默认收到 es2021，去掉旧浏览器
// downlevel 辅助代码。实测入口 80.3kB、LegalWorkspace 36.9kB、最大页面 chunk 85.3kB
// （SystemAnalyticsTabs）、总资产 1678.8kB，四项全部回到预算内。
// 注意：异步分组 chunk 也纳入页面 chunk 检查，避免把大块代码改个名字绕过预算。
const BUDGETS = {
  entryJsRawKB: Number(process.env.ENTRY_JS_RAW_KB || 100),
  entryJsGzipKB: Number(process.env.ENTRY_JS_GZIP_KB || 35),
  pageChunkRawKB: Number(process.env.PAGE_CHUNK_RAW_KB || 130),
  totalRawKB: Number(process.env.TOTAL_RAW_KB || 1700),
}

// 路由级页面 chunk 命名（router.js 懒加载视图）；System 的异步分组 chunk 也在这里检查。
const PAGE_CHUNK_PATTERN = /^(LegalWorkspace|System|SystemOpsTabs|SystemAnalyticsTabs|Documents|Agent|Tasks|Chat|Login|LegalPortal|LegalDeveloper|LegalOnboarding|Pricing)-/

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
  let warned = false
  for (const r of results) {
    const over = r.value > r.limit
    // 95% 预警档：还没撞线但只剩不到 5% 余量时先出 ⚠️，避免下一次改动才发现（不判失败）
    const tight = !over && r.value > r.limit * 0.95
    const flag = over ? '❌ 超预算' : tight ? '⚠️ 接近预算' : '✅'
    console.log(`${flag} ${r.label}: ${r.value.toFixed(1)} ${r.unit} (预算 ${r.limit} ${r.unit}，已用 ${((r.value / r.limit) * 100).toFixed(0)}%)`)
    if (over) failed = true
    if (tight) warned = true
  }
  if (reportOnly) process.exit(0)
  if (failed) {
    console.error('[bundle] 超出 bundle 预算：请按页面/入口拆分或按需加载后复检')
    process.exit(1)
  }
  console.log(warned ? '[bundle] 预算通过，但有项目已用超过 90%：下次改动前请先量产物' : '[bundle] 全部预算通过')
}

main()
