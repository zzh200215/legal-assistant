// 前后端联调路由扫描：逐视图渲染检查（白屏崩溃/JS错误/4xx-5xx API），逐路由即时打印
// 用法: node _route-sweep.mjs [username password]
import { chromium } from '@playwright/test'

const BASE = 'http://localhost:5173'
const API = 'http://127.0.0.1:8001'

const ROUTES = [
  '/legal-workspace',
  '/pricing',
  '/documents',
  '/chat',
  '/tasks',
  '/agent',
  '/system',
  '/legal-developer',
]

async function loginToken(username, password) {
  const r = await fetch(API + '/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  const j = await r.json()
  if (!j?.data?.access_token) throw new Error(`login failed: ${JSON.stringify(j).slice(0, 200)}`)
  return j.data.access_token
}

async function checkRoute(page, token, route) {
  const pageErrors = []
  const apiBad = []
  page.on('pageerror', (e) => pageErrors.push(String(e.stack || e).slice(0, 260)))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) {
      apiBad.push(`${r.request().method()} ${r.url().replace(API, '').replace(BASE, '')} -> ${r.status()}`)
    }
  })
  await page.addInitScript((t) => localStorage.setItem('token', t), token)
  let panel = null
  let navErr = null
  try {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded', timeout: 15000 })
    await page.waitForTimeout(1800)
    panel = await page.locator('#runtime-error-panel').textContent({ timeout: 2500 }).catch(() => null)
  } catch (e) {
    navErr = String(e.message).slice(0, 120)
  }
  const finalUrl = page.url().replace(BASE, '')
  const status = navErr ? 'NAV-ERR' : (panel || pageErrors.length) ? 'CRASH' : 'OK'
  const tag = status === 'OK' ? '✅' : '❌'
  console.log(`${tag} ${route} [${status}] ${finalUrl ? '→ ' + finalUrl : ''}`)
  if (navErr) console.log(`     NAV: ${navErr}`)
  if (panel) console.log(`     PANEL: ${panel.slice(0, 180)}`)
  for (const pe of pageErrors.slice(0, 2)) console.log(`     ERR: ${pe}`)
  for (const b of apiBad.slice(0, 6)) console.log(`     API: ${b}`)
  page.removeAllListeners()
}

async function sweep(user, pass, label) {
  const token = await loginToken(user, pass)
  const browser = await chromium.launch()
  console.log(`\n===== ${label} =====`)
  for (const route of ROUTES) {
    const page = await browser.newPage()
    try { await checkRoute(page, token, route) } catch (e) { console.log(`❌ ${route} [EXC] ${String(e.message).slice(0, 120)}`) }
    await page.close()
  }
  await browser.close()
}

async function main() {
  const [u, p] = process.argv.slice(2)
  const accounts = u && p
    ? [[u, p, `${u}`]]
    : [
        ['demo_lawyer', 'Demo@123456', 'demo_lawyer(user)'],
        ['admin', 'Admin@123456', 'admin'],
      ]
  for (const [user, pass, label] of accounts) {
    try { await sweep(user, pass, label) } catch (e) { console.log(`\n[${label}] login failed: ${e.message}`) }
  }
}

main()
