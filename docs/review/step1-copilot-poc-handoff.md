# 第一步交付说明：右侧法律助手（copilot）外壳 PoC

> 本文是 [2026-10-project-diagnosis.md](./2026-10-project-diagnosis.md) §6「7 天」第 4 条（copilot 外壳 PoC）的落地记录。
> 视觉基准：[ui-preview-direction-b.html](./ui-preview-direction-b.html)。

## 0. 范围与边界（访谈结论）

- **做**：认证外壳常驻右侧可收缩面板；「法律咨询」上下文接通一次问答；答案 + 可点击法源引用。
- **不做**（30 天项，本次明确排除）：四层体验（WS 过程时间线 / 逐句溯源 / 忠实度标记）、「展开为整页」、Chat 并入。
- **不做**：预览 HTML 里的整壳改造（左侧分组导航、顶栏面包屑、内容卡片化、统计卡、右侧栏）。本次只动右侧面板。
- 视觉方向沿用仓库里**已存在**的方向 B 令牌（`#4F46E5` + `#0D9488`），未重做。

## 1. 交付物

| 文件 | 说明 |
| --- | --- |
| `frontend/src/components/CopilotDock.vue` | 面板本体（原为 11 行空桩） |
| `frontend/src/components/copilot/CopilotAnswer.vue` | 答复块，懒加载 |
| `frontend/src/composables/useCopilotContext.js` | 上下文推导：纯函数 `deriveCopilotContext` + 薄 composable |
| `frontend/src/composables/__tests__/useCopilotContext.test.js` | 上述纯函数单测（7 例） |
| `frontend/tests/e2e/copilot-dock.spec.js` | 组件级 e2e（6 例，自带 mock，不依赖后端） |
| `frontend/src/App.vue` | 仅 copilot 栅格/断点相关行 |
| `scripts/copilot_consult_smoke.py` | 真实接口冒烟脚本（环境探测产物） |

## 2. 复用的既有资产（未重写）

`useLegalConsultations`（领域逻辑）、`EvidenceReference`（引用行）、`QueryStateView`（三态）、`AiOutputFeedback`（反馈）、`useLegalWorkspacePresentation`（标签映射）、`useStagedProgress`（阶段文案）、`useLegalSourceDetail`（法源条文）、`api/errors.js` 的 `normalizeError`、`design-tokens.css`（唯一取色来源）。

数据源：`POST /api/legal/consultations`，body `{ question, case_id? }`。

## 3. 验收证据（均已独立复现）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 构建 | `npm run build` | exit 0 |
| 静态检查 | `npm run lint` | exit 0，0 error / 48 warning（全部为既有 `vue/attributes-order`，不在本次文件内） |
| 单测 | `npm run test:unit` | 42 pass / 0 fail（含新增 7 例） |
| 组件级 e2e | `npx playwright test copilot-dock --workers=1` | 6 passed |
| 取色硬编码 | 正则扫描 4 个新增/改写文件 | clean |
| **打包预算** | `npm run bundle:check` | **exit 1 —— 见 §5 开口项 1** |

真实接口侧（独立探测，非本面板）：MySQL 8.0.46 库 `aibg`（156 表）+ Redis 6379 在宿主机运行，`GET /api/health` 三项全绿，`POST /api/legal/consultations` 真实 **200**、端到端 **25.2s**、真实 `glm-5.2` 输出。

## 4. 真实数据边界（重要）

`references[]` 实测有**两种稀疏形状**并存，渲染器必须容忍缺字段：

- **形状 A**（LLM 返回、未命中法源库，实测常见）：`title` / `citation` / `version` / `verification{verified, verification_note}` —— **没有 `source_id`**。
- **形状 B**（命中法源库）：在 A 之上多 `source_id` / `status` / `effective_date` / `jurisdiction`，`verification` 有 9 个键。

点击形状 A 的引用不会发出无效请求：`useLegalSourceDetail.js:16` 有 `if (!refItem?.source_id) return` 守卫。面板此时如实显示「未取到该法源的条文」。

⚠️ **当前 e2e 只覆盖了形状 B**，形状 A 仅有静态代码路径保证。

## 5. 开口项（需决策，未自行拍板）

1. **打包预算失败（会挂 CI）**：`.github/workflows/ci.yml:106-107` 跑 `npm run bundle:check`。
   实测：入口 JS raw **143.8/120**、入口 JS gzip **49.4/42**、总资产 **1622.0/1600**，三项全红。
   脚本自带标定注释记录 2026-10-10 实测为 118.5/120、40.1/42、1581.4/1600 —— 当时**通过**。
   即：本次改动把 gzip 与总资产两项从通过推成失败；raw 一项在 120 预算上本已贴线。
   可选处置：(a) 单开降 bundle 任务；(b) 按项目既有惯例**重新标定**预算并在 `check-bundle-budget.mjs` 注释里记录理由；(c) 暂不处理。
   **结局（2026-10-18）**：选 (a) 并做完——入口 raw 102.0/120、gzip 35.8/42、总资产 1579.0/1600 四项全绿，预算红线未重标定；详见 `docs/review/step10-bundle-budget.md`。
2. **案件上下文只来自 URL**：`LegalWorkspace` 在 URL 无 `case_id` 时会自动挑一个案件，此时面板显示「尚未绑定案件」，与页面显示不一致。理由：不为此新增请求。
3. **面板挂载会多发一次 `GET /legal/consultations`**：`useLegalConsultations` 内部的 `useQuery` 没有 `enabled` 开关；进入任一受保护路由都会发一次（非案件路由为无参列表）。

## 6. 顺带发现的既有缺陷（均未修复，属本次范围外）

- `frontend/src/api/errors.js` **`normalizeError` 不可重入**：`:135` 把返回值标为 `__normalized: true`，但 `:87` 的守卫要求它是**对象**，于是 二次规范化 会退化成「网络连接异常，请检查网络后重试」。任何"先规范化再传下游"的调用方都会踩到。
- `requirements.txt` **自身不可解**：`langchain-community==0.4.2` 要求 `pydantic-settings>=2.10.1`，与其中的 `pydantic-settings==2.6.1` 冲突。
- `requirements.lock` 编译于 Linux，含 Windows 不支持的 `uvloop`，直接 `uv pip sync` 会失败。
- `requirements-dev.txt` 换行符丢失/编码损坏，README 第 142 行的安装命令**装不出运行时依赖**。
- `useLegalSourceDetail` 的共享状态全仓库**没有任何组件渲染** —— 现有页面里点引用其实没有可见反馈。
- `frontend/src/utils/agentSocket.js` 是无导入方的死代码。

## 7. 未验证面（如实标注）

**面板 ↔ 真实后端从未在同一次会话里联通过。** 两侧各自都是真的（W1 用 mock 验组件，W2 用真实 HTTP 验接口），但"装配"是推理而非实测。要闭合需要在真实后端前跑一次面板端到端。

另注：`demo_lawyer` 的月度咨询配额已被真实调用用尽（现返 **429**），这是真实业务状态；`demo_reviewer` 仍可用。

## 8. 复现真实接口

```powershell
# 前置：MySQL(3306, 库 aibg) 与 Redis(6379) 在宿主机运行；.env 已配好；Docker 不需要
uv venv --python 3.11 .venv
# requirements.txt 不可解、lock 含 uvloop，故先剔除 uvloop 再装
uv pip sync --python .venv\Scripts\python.exe <过滤掉 uvloop 的 lock>
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
# 另开终端：
.venv\Scripts\python.exe scripts\copilot_consult_smoke.py http://127.0.0.1:8001 demo_reviewer "<密码>"
```

健康检查真实路径是 `/api/health`、`/api/health/live`、`/api/health/ready`（**不是** `/health`）。
