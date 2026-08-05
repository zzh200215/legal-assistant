# 定价/计费核对（#74，2026-09-01）

## 1. 定价口径核对（结论：统一为 ¥999/月）

| 来源 | 口径 | 核对结果 |
|---|---|---|
| subscription_plans（生产库，migration 0057 seed） | price_monthly：free 0 / pro 199 / team 999 | ✅ 事实基准 |
| subscription_service.py:188-206（PLAN_QUOTAS seed 同源） | 0 / 199 / 999（月） | ✅ 与 DB 一致 |
| enterprise-poc-sales-pack.md §5 | 团队版 999 元/月 × 折扣 | ✅ |
| september-commercialization-checklist.md | 团队版 999 元/月 | ✅ |
| pilot-success-playbook.md（原 **¥999/年** ×2 处） | 年 | ❌ **已修正为 ¥999/月**（本轮） |

## 2. 计费/发票能力盘点（组织维度对公开票，已具备）

- **LegalBilling.vue**（前端）+ legal_billing_api：billing_rules（时薪/固定/混合）× invoice 全生命周期（发票号、客户抬头、账期、金额、折扣、tax、PDF 路径、发送/收款/作废/红冲、幂等键、催收计数）
- 表：legal_billing_rules（0 条）/ legal_invoices / legal_payment_records（payment_method、provider、transaction_id、voucher_document_id）
- **用途**：律所对**客户**（client_display_name）开票的运营工具，与平台订阅收款是两条线。

## 3. 平台订阅收款路径（缺口）

| 环节 | 状态 |
|---|---|
| 套餐/配额 DB | ✅ |
| 配额消耗/拦截（quota_usages） | ✅ |
| 订阅状态 API（subscription_api /subscriptions） | ✅ 查询 |
| **订阅升级 API（checkout / webhook / cancel + 状态机 active/expired/cancelled）** | ✅ 已实现（2026-08-05 复核：webhook 验签 + 升级意图埋点已就绪；补全 expired 自动流转 beat 任务，测试 29 passed） |
| **前端套餐价格展示 + 购买入口** | ❌ 无（配额耗尽用户无自助升级 UI，也无余额/支付页面） |
| **支付渠道接入** | ❌ 无（payment_records.provider 为占位；无微信/支付宝/对公转账流程） |
| **发票（平台开给企业客户）** | ❌ 无（legal_invoices 是律所→其客户方向；平台收款开票未做） |
| 税务/抬头信息收集 | ❌ 无 |

## 4. 结论与 9 月动作

1. **定价文案已统一**（¥999/月），销售/文档口径一致；playbook D7 问卷题同步修正。
2. **支付路径为商业化硬缺口**，列入 9 月 checklist A 组：
   - P1：前端定价页（free/pro/team 三档 + 配额说明 + 购买按钮，埋点升级意图）
   - P1：订阅升级 API（user_subscriptions 状态机：active/expired/cancelled + 支付回调占位）— ✅ **已完成**（2026-08-05：checkout/webhook/cancel 已在 subscription_api 落地并补 expired 自动流转 beat 任务）
   - P2：对公转账收款流程（付款凭证上传 + 人工确认 + 开票登记），对接 LegalBilling 发票模型（platform 方向复用 invoice 结构）
3. 试点阶段不受影响（内测免费 + 配额供给），支付上线在 POC 转正式时点。

## 5. 关联

- #69 checklist 定价项：本报告为"正式开售前核对价格文案/发票/税务"的核对产出
- #63/#72 问卷 D7（付费意愿）：回收数据将直接决定 P1 支付路径优先级
