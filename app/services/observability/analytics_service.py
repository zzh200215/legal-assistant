"""观测分析门面：聚合各分析簇 mixin，保持既有单例接口不变。

原 ``AnalyticsService`` 按关注点拆为 LLM 用量 / 反馈 / 告警 / 运维任务 / 提示词实验
五个 mixin，本模块仅保留门面与 ``analytics_service`` 单例，对外方法名完全不变。
"""

from app.core.config import get_settings
from app.services.observability.alerts import AlertsMixin
from app.services.observability.feedback import FeedbackMixin
from app.services.observability.llm_analytics import LLMAnalyticsMixin
from app.services.observability.operations import OperationsMixin
from app.services.observability.prompt_eval import PromptEvalMixin

settings = get_settings()


class AnalyticsService(
    LLMAnalyticsMixin,
    FeedbackMixin,
    AlertsMixin,
    OperationsMixin,
    PromptEvalMixin,
):
    """观测分析门面：职责边界——只做聚合，不含具体分析逻辑。"""


analytics_service = AnalyticsService()
