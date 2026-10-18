# Step 12：LLM 网关可靠性（E 项）——跨事件循环连接池根因修复 + 超时有界

- 日期：2026-10-18
- 对应：诊断文档 E 项「排查 LLM 网关可靠性（`Event loop is closed` / 长时间不返回）」
- 前置：`docs/review/step7-copilot-server-trace.md`（trace 让降级可见——本项能定位，靠的就是 trace 里的 `degraded`）

## 0. 现象与根因

**现象**：真机上连续咨询，第 2 次经常在 ~20ms 内返回降级结果（规则兜底），后端日志：

```
LLM call failed, falling back to deterministic logic: Event loop is closed
```

**根因**（traceback 路径）：`httpcore/_async/http11.py` → `_response_closed()` → `anyio/_backends/_asyncio.py:1397` → `proactor_events.py:109 close()` → `base_events.py:762 _check_closed()` 抛 `RuntimeError: Event loop is closed`。

`ModelGateway` 的连接池按 `(provider, base_url, api_key 指纹, timeout)` 缓存 `httpx.AsyncClient`，**没把事件循环算进键**。后台线程里的一次性 `asyncio.run(...)`（如 Chroma 侧嵌入）关掉自己的临时循环后，池子里留下的 keep-alive 连接属于那个**已关闭的循环**；主循环复用它即炸。

## 1. 修复

`app/core/llm_client.py`：

- `_clients` 的值由 `httpx.AsyncClient` 改为 `(创建它的事件循环, httpx.AsyncClient)`；
- `_get_client(target, *, timeout)` 取当前 `asyncio.get_running_loop()`，**循环不同就丢弃旧 client 重建**（注释写明原因）；
- `close()` 只 `aclose()` 那些"自己循环仍未关闭"的 client，跳过已关闭循环的（否则 close 本身会再抛一次）。

回归测试：`tests/test_llm_client_compat.py::ClientPoolEventLoopTests`（3 例：同循环复用 / 换循环换新 / close 跳过已关闭循环的 client）。

**顺带修掉一条失效的网关测试**：`tests/test_circuit_breaker.py::test_embed_raises_circuit_open_when_primary_open` 一直失败——它把熔断失败记在 `primary_target` 上，而 `embed()` 自「embedding 目标拆分」（聊天与向量可分属不同平台，如聊天走商汤、向量走 dashscope）之后用的是 `embedding_target` 的熔断键，于是断言永远不成立。改为按 `embedding_target` 记失败并重命名用例（`..._when_embedding_target_open`），3 个网关测试文件 33 passed。

## 2. 真实数据（修复前后对照）

从已落库的 `legal_consultations.trace_json` 聚合（24 条带 trace 的咨询，本机真后端）：

| id 区间 | 生成步状态 |
| --- | --- |
| 19–32（修复前） | 8 success / 5 degraded —— 降级耗时 **17–90ms**（即"秒回降级"，正是本 bug 的指纹） |
| 33–42（修复后） | **10 success / 0 degraded** |

成功生成的耗时分布（n=19）：min **6.34s**、p50 **14.19s**、mean 14.9s、max **31.66s** —— 均在配置的 60s 超时之内，**没有出现"长时间不返回"**。

## 3. "长时间不返回"的边界（有界性证据）

`tests/test_legal_gateway_resilience.py`（新增 6 例）：

1. `_llm_chat` 在网关 `httpx.ReadTimeout` / `httpx.ConnectError` 时**吞掉异常返回 None**（异常不穿透到接口层）；
2. 咨询链路在网关超时时**仍返回可用答复**（规则兜底），且 `trace.steps[generate].status == "degraded"`、detail 含"规则兜底"，`metrics` 照常记录（降级率可统计）；
3. 客户端**必须带配置里的超时创建**（`LLM_REQUEST_TIMEOUT_SECONDS`，默认 60s，可用配置覆盖）；
4. 重试预算有界（primary 1–5、fallback 1–3），不会把"不返回"放大成无限等待。

即：**最坏情况是"等待 ≤ 超时 × 重试次数后降级"，不是无限挂起**；且降级是如实标注的，不会假装成功。

## 4. 如实说明

1. **降级率仍有 5/24（20.8%）**，但全部发生在修复前的 id ≤ 32；修复后连续 10 次无降级。样本小，不能断言"再也不会降级"。
2. **网关侧的限流/超时是外部风险**：本机是免费额度网关（`glm-5.2`），我们只能保证"有界 + 如实降级"，不能保证第三方 SLA。
3. **max 31.66s 的一次生成**说明慢响应确实存在；60s 超时是当前的兜底阈值，如果产品要求更短的等待，需要调 `LLM_REQUEST_TIMEOUT_SECONDS` 并接受更高的降级率——这是产品取舍，不是 bug。
4. 修复只覆盖 `ModelGateway` 的**连接池**；`chat_stream` 等路径复用同一池，同样受益（未单独压测）。
5. 后端回归子集（`-k "legal or llm or gateway or consult or reference or refusal"`）**578 passed / 2 failed**，两条失败都不属于本项：一条是上面那条已修的失效熔断测试（修后 33 passed），另一条是既有的环境不一致 `tests/test_legal_encryption.py::test_aes_gcm_ciphertext_does_not_expose_plaintext`（断言密文前缀 `enc:v1:`，而本机 `.env` 为 `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION=v2`，该用例未像同文件其它用例那样 patch `_settings()`）。

## 5. 下一步

- 降级率做成可观测指标（`trace.metrics` 已有数据，缺的是聚合看板）。
- 若要缩短等待：把超时降到 20–30s 并观察降级率变化（需要产品决策）。
- 给网关加"熔断"（连续 N 次失败后短期直连兜底），避免每次请求都白等一个超时。
