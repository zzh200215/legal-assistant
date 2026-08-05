# 飞书企业自建应用接入指南（#87/M-5，2026-08-05）

> 用途：飞书 M1-M4 代码管线已就绪（app/services/feishu_service.py，48 项测试），本指南是把「出站占位」切换为「真实出站」的唯一前提文档。
> 前置：企业管理员权限（开通开发者 + 审核应用版本）。预计一次接入 30 分钟（不含审核等待）。
> 对接代码：`app/api/feishu_api.py`（绑定 + 回调）、`app/services/feishu_service.py`（事件处理 + 出站）、`app/core/config.py`（凭据配置）。
> ⚠️ 接入第一步：**修正回调签名校验算法**（见 §6），现有实现为简化版，与飞书线上签名不一致。

## 1. 申请企业自建应用

1. 企业管理员登录飞书 → 管理后台 → 开通开发者权限（或进入 https://open.feishu.cn 创建）。
2. 开发者后台 → 创建应用 → 选择「企业自建应用」→ 填写名称（如「律智检」）、图标、描述。
3. 创建后进入应用详情 → 「凭证与基础信息」：
   - **App ID**（形如 `cli_xxxxxxxx`）
   - **App Secret**（用于获取 tenant_access_token）
4. 记录后写入 `.env`（见 §7）。

> 前提：需企业管理员在管理后台开通「开发者权限」；自建应用仅对本企业成员可见可用。

## 2. 权限申请（权限管理）

| 权限 | 用途 | 对应代码 |
|---|---|---|
| `im:message` | 发送单聊/群组消息（出站卡片/文本） | FeishuMessenger._send_message |
| `im:message.p2p_msg:readonly` | 接收单聊文本/文件消息（im.message.receive_v1） | extract_message_event / extract_file_event |
| `im:file` | 下载消息文件（im/v1/files） | FeishuMessenger.download_file |
| `im:message.group_at_msg:readonly` | 群 @ 消息（可选，M1 群聊场景预留） | — |

申请后需「创建版本并发布」，企业管理员审核通过后权限生效。

## 3. 事件订阅配置

应用详情 → 「事件与回调」：

- 订阅方式：使用**请求地址**（回调模式；长连接 WebSocket 模式与当前代码不兼容，勿选）。
- **请求地址**：`https://<你的域名>/api/feishu/callbacks/event`
  - 说明：FastAPI 已把 feishu router 挂在 `/api/feishu`，该路由即 `app/api/feishu_api.py` 的 `feishu_event_callback`。
- **加密策略**：开启 → 设置 **Encrypt Key**（自定字符串，越随机越好）→ 写入 `.env` 的 `FEISHU_EVENT_ENCRYPT_KEY`。
- **订阅事件**：
  - `im.message.receive_v1`（接收文本/文件消息）
  - `card.action.trigger`（卡片按钮交互，M3 审核/文书按钮回传）
  - `app.status_change`（可选，应用启停通知）

保存时飞书会发送 `url_verification` 请求验证地址连通，当前代码已支持回显 challenge（`handle_event` → `url_verification` 分支）。

## 4. 事件载荷与现有处理对照

| 事件 | 载荷关键字段 | 现有处理 |
|---|---|---|
| url_verification | `type` + `challenge`（或加密包裹） | 回显 challenge |
| im.message.receive_v1（文本） | `event.message.content` → `{"text"}`；`event.sender.sender_id.open_id` | extract_message_event → M1 咨询 / M3 文书 / 审核队列 |
| im.message.receive_v1（文件） | `event.message.message_type=file`；content → `{"file_key","file_name"}` | extract_file_event → M2 合同初筛 |
| card.action.trigger | `event.operator.operator_id.open_id`；`event.action.value`（自定 value） | extract_card_action → handle_card_action（review/draft 路由） |

## 5. 出站与文件（凭据接入后生效）

### 5.1 发送消息
- 获取令牌：`POST {BASE}/auth/v3/tenant_access_token/internal`，body `{"app_id","app_secret"}` → `tenant_access_token`（7200s 缓存）。
- 发消息：`POST {BASE}/im/v1/messages`，query `receive_id_type=open_id`，header `Authorization: Bearer <token>`，
  body `{"receive_id":"<open_id>","msg_type":"text|interactive","content":"<JSON 字符串>"}`。
- 现有代码：FeishuMessenger.send_card / send_text（已带 `receive_id_type=open_id`）。

### 5.2 下载文件（M2）
- `GET {BASE}/im/v1/files/{file_key}`，query `type=file`，header `Authorization: Bearer <token>` → 文件二进制。
- 现有代码：FeishuMessenger.download_file。

## 6. ⚠️ 回调签名校验（接入时必须核对/修正）

飞书事件回调的签名校验现行两套，**现有代码实现的是简化版（hex），与飞书线上发送的 base64 签名不一致**，真实接入时回调将被拒（400 INVALID_FEISHU_SIGNATURE）。

### 6.1 现有实现（app/api/feishu_api.py `_verify_callback_signature`）
```python
expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()  # hex，旧简化版
```
仅比对请求头 `X-Lark-Signature`，用 encrypt_key 对原始 body 做 HMAC-SHA256 的 **hex**。

### 6.2 飞书现行校验（V2，推荐）
请求头：`X-Lark-Request-Timestamp`、`X-Lark-Request-Nonce`、`X-Lark-Signature`。
```text
signature = base64(HmacSHA256(timestamp + nonce + encrypt_key + raw_body, encrypt_key))
```
### 6.3 V1 旧版
```text
signature = base64(HmacSHA256(raw_body, encrypt_key))   # base64，非 hex
```

### 6.4 接入时改动建议
- 新增配置 `FEISHU_CALLBACK_VERIFY=auto|v2|v1|off`（默认 `auto`）。
- `_verify_callback_signature` 按 V2（优先）→ V1 → 现有 hex 兜底顺序校验，任中即通过；`off` 用于临时排查。
- **校验逻辑需以接入时飞书官方文档为准**（签名串拼接、编码可能在版本间微调），本指南只给方向性结论。
- 建议用飞书开发者后台「事件订阅」页的调试工具，先跑通 `url_verification` 再测消息事件。

## 7. 环境变量配置（.env）

```ini
FEISHU_APP_ID=cli_xxxxxxxx            # 凭证与基础信息 → App ID
FEISHU_APP_SECRET=xxxxxxxx            # 凭证与基础信息 → App Secret
FEISHU_EVENT_ENCRYPT_KEY=xxxxxxxx     # 事件订阅 → 加密策略 → Encrypt Key
# 可选
STRUCTURED_LOG_JSON_LINES=true        # 等保日志汇聚（对接 SLS/SIEM 时开）
```

配置后无需改代码：出站 `FeishuMessenger` 检测到 `FEISHU_APP_ID/SECRET` 非空即自动从「占位禁用」切换为真实发送；回调在 `FEISHU_EVENT_ENCRYPT_KEY` 配置后即按加密载荷解密。

## 8. 端到端测试清单（凭据接入后）

- [ ] `url_verification`：开发者后台保存事件订阅时回调地址校验通过（返回 challenge）。
- [ ] 绑定：Web 端「设置-飞书绑定」用 open_id 调 `POST /api/feishu/bindings` 成功。
- [ ] M1：给机器人单聊发「工伤赔偿怎么算」→ 收到法条核对卡片。
- [ ] M2：单聊发 `.docx` 合同 → 收到风险条款卡片（≤20MB）。
- [ ] M3 审核：回复「待审核」→ 收到待审核卡片 → 点「通过/退回」→ 卡片回传 Web 审核队列状态一致。
- [ ] M3 文书：发「文书 劳动仲裁申请书 申请人:张三」→ 收到草稿卡片。
- [ ] M4 提醒：次日 09:00 beat 任务触发后，未活跃用户收到激活卡片。
- [ ] 权限回归：非企业成员 open_id 无绑定 → 收到「请先绑定」提示。

## 9. 关联

- feishu-plugin-spec.md（M1-M4 需求规格）
- app/services/feishu_service.py（管线实现，48 项测试）
- 待完成任务清单 §5.2（M-5 飞书插件）
