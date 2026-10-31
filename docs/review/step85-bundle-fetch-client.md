# 第八十五步交付说明：包体治理——用原生 fetch 替换 axios

> 一句话：请求层从 axios 换成约 200 行的原生 fetch 客户端（同样的认证/超时/401 单飞刷新/envelope 解包/Blob/FormData 合约），删掉 axios 及 24 个传递依赖，总资产从 **1687.9 → 1643.9 KB**，余量从 **12.1 → 56.1 KB**。

- 日期：2026-10-31
- 前端：`src/api/http.js`（重写）、`package.json`、`package-lock.json`（删除依赖）
- 测试：新增 401 单飞刷新 e2e；全量 mock e2e **60 passed / 1 skipped**；前端单测 **100 passed**
- 后端/接口/迁移：无改动；OpenAPI 仍 **370**
- 包体：入口 82.7 / 100（gzip 28.7 / 35），最大页面 chunk 85.3 / 130，总资产 **1643.9 / 1700 KB**

---

## 1. 为什么动请求层

step84 结束时总资产 1687.9 KB，只剩 12.1 KB，任何页面级功能都会超预算。逐项量产物后，最大的可移除单点依赖是 `http-vendor`（axios）**45.6 KB**：

- 全仓 246 处调用只用 `get/post/put/patch/delete`；
- 没有上传进度、请求拦截器扩展、`validateStatus` 等 axios 高级能力；
- 401 单飞刷新已经是独立纯逻辑（`api/refresh.js`），可以原样复用。

## 2. 新客户端

`src/api/http.js` 保持原有导出（`default http`、`getAccessToken`、`setAccessToken`、`getRefreshToken`、`setRefreshToken`、`idempotencyHeaders`），内部改为 fetch：

| 能力 | 做法 |
| --- | --- |
| baseURL / 查询串 | `/api` 前缀；`URLSearchParams`，跳过 `undefined/null`，数组重复 key |
| 认证 | 每次请求从 localStorage 读 token，注入 `Authorization: Bearer …` |
| 超时 | 60s `AbortController`，覆盖到响应体读取结束 |
| 取消 | 透传查询层的 `signal`，取消映射为 `ERR_CANCELED` |
| 401 | 复用 `createRefreshCoordinator` 单飞刷新；成功后重放原请求（`_retried` 防二次刷新） |
| 解包 | 有 `success` 字段取 `data`，否则原样返回 |
| 请求体 | 对象 → JSON + `Content-Type`；`FormData`/`Blob`/字符串原样 |
| 响应体 | JSON / 文本 / `responseType: 'blob'`；204/205 返回 null |
| 错误 | 构造 `error.response = { status, data, rawData, headers }`；422 拼字段错误、429 默认文案 |
| 头信息 | 统一小写键，`etag` / `content-disposition` 读取方式不变 |

## 3. 依赖变化

- `package.json` 移除 `axios`；
- `package-lock.json` 减少 25 个包（axios + follow-redirects / form-data / proxy-from-env 等传递依赖），311 行；
- 构建产物 `http-vendor-*.js` 消失，入口因新客户端代码增加约 1.6 KB，但总资产净减 **44 KB**。

## 4. 验证

包体（同一棵树）：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 入口 JS | 81.1 KB | 82.7 KB |
| 入口 gzip | 28.1 KB | 28.7 KB |
| 最大页面 chunk | 85.3 KB | 85.3 KB |
| 总资产 | 1687.9 KB | **1643.9 KB** |
| 余量 | 12.1 KB | **56.1 KB** |

e2e：

- 新增「401 单飞刷新」用例：两个并发文档请求都返回 401 → 只发一次 `/auth/refresh` → 两个请求各自重放并带 `Bearer fresh-token`，localStorage 的 access/refresh 都更新；
- 全量 mock e2e（排除 `verify-*` 真实后端用例）：**60 passed / 1 skipped**，覆盖登录、合同上传 multipart、docx/blob 下载、429、通知、审核队列、门户、System 页等；
- 顺手补齐 6 个 spec 缺失的 `/legal/history-counts` mock，以及 u3 的 `GET /legal/consultations/991` mock——这些是 step76 后遗留的既有失败，与本次替换无关，修完 e2e 才全绿。

| 项 | 结果 |
| --- | --- |
| 全量 mock e2e | 60 passed / 1 skipped |
| 前端单测 | 100 passed |
| 前端 lint | 0 error（12 条既有 warning） |
| OpenAPI / 迁移 | 370（无变更）/ 无（head 仍 `20261030_0114`，115 个） |

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.3 分钟，exit 0**（本树最终状态）。
- 后端全量：**2482 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.7 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1643.9 / 1700 KB**。

## 6. 边界

1. **不是 axios 全兼容层**：只实现本仓用到的子集；没有公开拦截器、`validateStatus`、上传进度、XHR 适配。
2. **错误对象是仿形状**：提供 `response/status/data/config`，但不保证 AxiosError 的全部字段；页面统一走 `normalizeError`。
3. **401 刷新规则沿用旧逻辑**：公开门户、通知轮询、`/auth/refresh` 本身不触发刷新；重放后仍 401 才登出。
4. **客户端不做自动重试**：GET 重试仍由查询层负责，写操作不重试，避免重复提交。
5. **超时是整体 60s**：包含响应体读取；没有按接口单独配置。
6. **查询串数组用重复 key**，不做 axios 的 `[]` 括号序列化；当前调用没有数组参数。
7. **Blob 错误响应按 JSON/文本解析**：比 axios 直接给 Blob 更利于展示错误，但属于行为差异。
8. **包体预算仍按 raw 统计**：gzip 入口只增 0.6 KB，总资产 raw 减 44 KB。
9. **无后端改动**：认证、刷新、幂等头契约不变。
10. **e2e 修的是既有缺口**：history-counts / 991 mock 与本次替换无因果，但补齐后 mock 套件才全绿。

## 7. 后续建议

- 余量恢复到 56.1 KB，下一步可以继续做业务功能；
- 若后续需要上传进度，再评估是否只对上传路径引回轻量 XHR，而不是整体依赖；
- 把「全量 mock e2e」纳入可选门禁（当前 `--e2e` 需后端 :8001）。
