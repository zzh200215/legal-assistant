"""P1 可观测性：进程内指标快照 + 预聚合统计表。

- ops_metric_snapshots：进程内 metrics facade 按窗口快照（API p95 直方图 / 计数 / gauge）。
- ops_metric_hourly / ops_metric_daily：按时间桶 + org + 有限枚举标签的预聚合（SLO 报表数据源）。
- ops_metric_watermarks：每 (granularity, metric_name) 的最后完成桶，聚合任务断点恢复/幂等推进。

设计约束：
- 金额一律 Numeric/Decimal，禁止 float（model_cost 等）。
- 标签只允许有限枚举（model/channel/queue/status/error_category 等），
  禁止 request_id/trace_id/user_id/document_id 等高基数字段。
- 聚合按 (bucket, metric_name, org_id, labels_hash) 幂等 upsert：重复执行不重复累加。
"""

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.core.database import Base


class OpsMetricSnapshot(Base):
    """进程内指标窗口快照（snapshot 粒度，供小时/天级聚合与短期诊断）。"""

    __tablename__ = "ops_metric_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    bucket_start = Column(DateTime(timezone=True), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False, index=True)
    org_id = Column(Integer, nullable=True, index=True, comment="organization_id，NULL=平台级")
    kind = Column(String(16), nullable=False, default="counter",
                  comment="counter / histogram / gauge")
    labels_json = Column(Text, nullable=True, comment="有限枚举标签 JSON（不含高基数 ID）")
    count = Column(Numeric(20, 6), nullable=False, default=0)
    sum_value = Column(Numeric(20, 6), nullable=True, comment="sum_ms（耗时类）/ 求和值")
    p95_value = Column(Numeric(20, 6), nullable=True, comment="p95 毫秒（histogram）")
    numerator = Column(Numeric(20, 6), nullable=True)
    denominator = Column(Numeric(20, 6), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("bucket_start", "metric_name", "org_id", "kind", "labels_json",
                         name="uq_ops_metric_snapshots_bucket"),
    )


class OpsMetricHourly(Base):
    """小时桶预聚合。"""

    __tablename__ = "ops_metric_hourly"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    bucket_start = Column(DateTime(timezone=True), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False, index=True)
    org_id = Column(Integer, nullable=True, index=True)
    labels_json = Column(Text, nullable=True)
    count = Column(Numeric(20, 6), nullable=False, default=0)
    sum_value = Column(Numeric(20, 6), nullable=True)
    max_value = Column(Numeric(20, 6), nullable=True, comment="gauge 类（积压量）窗口内最大值")
    p95_value = Column(Numeric(20, 6), nullable=True)
    numerator = Column(Numeric(20, 6), nullable=True)
    denominator = Column(Numeric(20, 6), nullable=True)
    cost_value = Column(Numeric(20, 6), nullable=True, comment="成本（Decimal，禁止 float）")
    source_watermark = Column(String(128), nullable=True, comment="来源明细 max(id) 或时间戳")
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("bucket_start", "metric_name", "org_id", "labels_json",
                         name="uq_ops_metric_hourly_bucket"),
    )


class OpsMetricDaily(Base):
    """天桶预聚合（SLO 报表主数据源）。"""

    __tablename__ = "ops_metric_daily"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    bucket_start = Column(DateTime(timezone=True), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False, index=True)
    org_id = Column(Integer, nullable=True, index=True)
    labels_json = Column(Text, nullable=True)
    count = Column(Numeric(20, 6), nullable=False, default=0)
    sum_value = Column(Numeric(20, 6), nullable=True)
    max_value = Column(Numeric(20, 6), nullable=True)
    p95_value = Column(Numeric(20, 6), nullable=True)
    numerator = Column(Numeric(20, 6), nullable=True)
    denominator = Column(Numeric(20, 6), nullable=True)
    cost_value = Column(Numeric(20, 6), nullable=True)
    source_watermark = Column(String(128), nullable=True)
    schema_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("bucket_start", "metric_name", "org_id", "labels_json",
                         name="uq_ops_metric_daily_bucket"),
    )


class OpsMetricWatermark(Base):
    """聚合水位线：每 (granularity, metric_name) 已完成聚合的最后一个桶起点。"""

    __tablename__ = "ops_metric_watermarks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    granularity = Column(String(8), nullable=False, comment="hour / day")
    metric_name = Column(String(128), nullable=False)
    last_bucket = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("granularity", "metric_name", name="uq_ops_metric_watermarks_key"),
    )
