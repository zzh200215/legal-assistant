"""Background task / agent runtime settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class TaskSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    AGENT_TOOL_TIMEOUT_SECONDS: int = Field(default=45, ge=5, le=300)
    AGENT_PARALLEL_MAX_WORKERS: int = Field(default=2, ge=1, le=4)

    # Agent Run 可靠性：run/step 超时、工具重试、审批过期、幂等。
    # RUN 级在步边界检查；STEP 级是单步墙钟预算，约束步内 LLM 与工具调用
    # （见 AgentService._step_budget_seconds）。MAX_RETRIES 是全局上限，只能收紧
    # 工具契约声明的重试次数；BACKOFF 是契约未声明时的退避基数。
    AGENT_RUN_DEADLINE_SECONDS: int = Field(default=600, ge=30, le=86400)
    AGENT_STEP_DEADLINE_SECONDS: int = Field(default=120, ge=10, le=3600)
    AGENT_TOOL_MAX_RETRIES: int = Field(default=1, ge=0, le=5)
    AGENT_TOOL_BACKOFF_BASE_SECONDS: int = Field(default=2, ge=1, le=60)
    AGENT_APPROVAL_EXPIRE_SECONDS: int = Field(default=3600, ge=60, le=604800)
    AGENT_TOOL_IDEMPOTENCY_ENABLED: bool = Field(default=True)
    # 内部 A2A 控制面：防止 Agent 递归委派放大资源与权限风险。
    A2A_MAX_DELEGATION_DEPTH: int = Field(default=3, ge=1, le=10)

    # 图 checkpoint 保留：一次问答/一次 Run 一个 thread，不清理则 sqlite 只增不减。
    # 窗口内保留是为了回放与断点续跑，窗口外的 thread 已无人可用。
    GRAPH_CHECKPOINT_PRUNE_ENABLED: bool = Field(default=True)
    GRAPH_CHECKPOINT_RETENTION_DAYS: int = Field(default=7, ge=1, le=365)

    # 文档处理任务：重试策略与 lease（租约）回收。
    # RENEW_INTERVAL 驱动 _LeaseHeartbeat 的后台续约节奏，并给阶段边界的显式续约去重；
    # 必须明显小于 TTL——单个阶段超过 TTL 就会被回收任务当作失联重新排队。
    DOCUMENT_TASK_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    DOCUMENT_TASK_BACKOFF_BASE_SECONDS: int = Field(default=5, ge=1, le=3600)
    DOCUMENT_JOB_LEASE_TTL_SECONDS: int = Field(default=300, ge=30, le=86400)
    DOCUMENT_JOB_LEASE_RENEW_INTERVAL_SECONDS: int = Field(default=60, ge=10, le=3600)
