# 配置管理指南

## 概述

本项目使用基于 Pydantic 的配置管理系统，支持环境变量和 `.env` 文件配置。

## 快速开始

### 1. 创建配置文件

```bash
# 复制示例配置
cp .env.example .env
```

### 2. 配置必需项

编辑 `.env` 文件，至少配置以下必需项：

```env
# 安全密钥（至少32字符）
SECRET_KEY=your-strong-random-secret-key-here

# LLM API密钥
LLM_API_KEY=your-dashscope-api-key-here

# 管理员账号
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-admin-password
```

### 3. 运行配置检查

```bash
# 诊断配置问题
python scripts/check_config.py
```

## 配置项说明

### 核心配置

#### 数据库配置

```env
# MySQL数据库连接
DATABASE_URL=mysql+pymysql://root:password@localhost:3306/aibg

# Docker环境使用容器主机名
DATABASE_URL_DOCKER=mysql+pymysql://root:password@mysql:3306/aibg
```

#### Redis配置

```env
REDIS_URL=redis://localhost:6379/0
```

#### 安全密钥配置

```env
# JWT签名密钥（必需，至少32字符）
SECRET_KEY=your-secret-key

# 连接器凭证加密密钥（可选，为空时从SECRET_KEY派生）
# 生成方法：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
CONNECTOR_CREDENTIAL_ENCRYPTION_KEY=

# 法律数据加密密钥（可选）
LEGAL_DATA_ENCRYPTION_KEY=
```

### LLM配置

```env
# 提供商类型：openai_compatible 或 ollama
LLM_PROVIDER=openai_compatible

# API配置
LLM_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-api-key

# 模型选择
LLM_MODEL=qwen-plus
EMBEDDING_MODEL=text-embedding-v3

# 短、低风险文本默认优先走小模型；复杂/法律/Agent/RAG 请求固定使用 LLM_MODEL。
# 小模型地址和密钥为空时复用主模型的 OpenAI 兼容地址及密钥。
# qwen-turbo 仅是千问小模型示例，免费额度以账号实际套餐为准。
LLM_MODEL_ROUTING_ENABLED=true
LLM_SMALL_MODEL=qwen-turbo
LLM_SMALL_MODEL_PROVIDER=openai_compatible
LLM_SMALL_MODEL_API_BASE_URL=
LLM_SMALL_MODEL_API_KEY=
LLM_SIMPLE_REQUEST_MAX_CHARS=600
LLM_PRIMARY_REQUEST_RETRIES=2
LLM_FALLBACK_REQUEST_RETRIES=1
LLM_REQUEST_TIMEOUT_SECONDS=60
LLM_MODEL_FALLBACK_ENABLED=true
LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY=true
LLM_ROUTING_ALERT_MIN_REQUESTS=10
LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE=0.20
LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE=0.30

# 定价配置（JSON格式）
LLM_MODEL_PRICING={"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012}}
```

模型降级只针对连接、读取超时、协议异常和服务端 5xx：主模型失败后切换小模型；短请求的小模型失败可回切主模型。4xx 参数/鉴权错误以及本地限流、Token 预算拒绝不会绕过治理。流式回答只有在第一个输出分片前失败时才切换模型。

管理员可通过 `GET /api/analytics/llm-routing/stats` 查看路由后的运行统计：小模型首选命中率、主模型初次调用失败率、降级次数与成功率、按模型成本占比，以及按 action 的尝试平均耗时。统计依赖迁移 `20260730_0051` 后新增的调用日志字段；迁移前的历史日志保留可查，但不会纳入这些路由指标。

`GET /api/analytics/llm-routing/health` 返回最近 1–24 小时的路由健康快照。窗口内请求数达到 `LLM_ROUTING_ALERT_MIN_REQUESTS` 后，主模型初次调用失败率达到 `LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE`，或备用模型失败率达到 `LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE`，将返回 `degraded`；运营告警任务会将其作为脱敏高优先级告警推送至已配置的 Webhook。

### 向量数据库配置

```env
# 提供商：chroma 或 qdrant
VECTOR_STORE_PROVIDER=chroma

# Chroma配置
CHROMA_PERSIST_DIR=./data/chroma_db

# Qdrant配置（使用qdrant时需要）
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your-qdrant-key
```

### 管理员配置

```env
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=strong-password-here
```

## 配置验证

系统在启动时会自动验证配置：

### 必需配置检查

- `SECRET_KEY`: 至少32字符
- `LLM_API_KEY`: 至少16字符，不能为示例值
- 加密密钥长度验证

### JSON格式验证

- `LLM_MODEL_PRICING`: 必须为有效JSON对象
- `SIGNING_WEBHOOK_SECRETS_JSON`: 必须为有效JSON对象

### 生产环境检查

运行 `scripts/check_config.py` 会检查：

- 是否使用SQLite（生产环境建议MySQL/PostgreSQL）
- 是否配置Redis
- 管理员账号是否配置

## 使用配置诊断工具

```bash
# 运行完整诊断
python scripts/check_config.py
```

诊断工具会检查：

1. **环境文件检查**: 确认 `.env` 文件存在
2. **配置加载检查**: 验证配置是否能正确加载
3. **健康状态检查**: 检查生产环境必需配置
4. **密钥生成**: 提供安全密钥生成示例

### 输出示例

```
============================================================
  配置健康检查
============================================================
✓ 整体状态: HEALTHY
  使用配置文件: /path/to/.env

✓ 所有配置检查通过
```

## 常见问题

### 1. 配置加载失败

**错误**: `配置加载失败：LLM_API_KEY必须配置有效的API密钥`

**解决**:
- 检查 `.env` 文件是否存在
- 确认 `LLM_API_KEY` 不是示例值（如 `your-api-key`）
- 确认API密钥长度至少16字符

### 2. SECRET_KEY验证失败

**错误**: `SECRET_KEY长度至少需要32字符以确保安全性`

**解决**:
```bash
# 生成强随机密钥
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 将生成的密钥复制到.env文件
SECRET_KEY=生成的密钥
```

### 3. JSON格式错误

**错误**: `LLM_MODEL_PRICING格式错误`

**解决**:
- 确保值是有效的JSON格式
- 不要有多余的逗号或引号
- 可以使用在线JSON验证工具检查

### 4. 生产环境警告

**警告**: `使用默认SQLite数据库，生产环境请配置MySQL/PostgreSQL`

**解决**:
```env
# 配置MySQL
DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname
```

## API密钥安全

### 最佳实践

1. **不要提交实际密钥到版本控制**
   - `.env` 文件已在 `.gitignore` 中
   - 只提交 `.env.example` 模板

2. **使用强随机密钥**
   ```bash
   # SECRET_KEY
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   
   # Fernet密钥
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. **定期轮换密钥**
   - 生产环境建议定期更换API密钥
   - 配置独立的加密密钥便于轮换

4. **权限控制**
   ```bash
   # Linux/Mac: 限制.env文件权限
   chmod 600 .env
   ```

## 环境变量优先级

配置加载顺序（后者覆盖前者）：

1. 代码中的默认值
2. `.env` 文件
3. 系统环境变量

示例：
```bash
# 临时覆盖配置
LLM_MODEL=qwen-turbo python -m uvicorn app.main:app
```

## 开发环境 vs 生产环境

### 开发环境

```env
DATABASE_URL=sqlite:///./data/app.db
DATABASE_ECHO=true  # 显示SQL日志
LLM_PROVIDER=ollama  # 使用本地模型
```

### 生产环境

```env
DATABASE_URL=mysql+pymysql://user:pass@host:3306/db
DATABASE_ECHO=false
LLM_PROVIDER=openai_compatible
# 配置所有必需的加密密钥
CONNECTOR_CREDENTIAL_ENCRYPTION_KEY=...
LEGAL_DATA_ENCRYPTION_KEY=...
```

## 代码使用示例

### 获取配置

```python
from app.core.config import get_settings

settings = get_settings()
print(settings.LLM_MODEL)
```

### 健康检查

```python
from app.core.config import check_config_health

health = check_config_health()
print(health['status'])  # healthy, warning, unhealthy, error
print(health['issues'])  # 问题列表
print(health['warnings'])  # 警告列表
```

## 配置扩展

如需添加新配置项：

1. 在 `app/core/config.py` 的 `Settings` 类中添加字段
2. 在 `.env.example` 中添加说明和默认值
3. 如需验证，添加 `@field_validator` 装饰器
4. 更新本文档

示例：
```python
class Settings(BaseSettings):
    # 新增配置
    NEW_CONFIG_ITEM: str = "default_value"
    
    @field_validator("NEW_CONFIG_ITEM")
    @classmethod
    def validate_new_config(cls, v: str) -> str:
        if not v:
            raise ValueError("NEW_CONFIG_ITEM不能为空")
        return v
```

## 相关文件

- `app/core/config.py`: 配置类定义和验证逻辑
- `.env.example`: 配置模板
- `scripts/check_config.py`: 配置诊断工具
- `docs/CONFIG.md`: 本文档
