FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# P1-E：锁文件（含哈希）保证镜像依赖可复现；--require-hashes 校验完整性
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# 备份任务依赖 mysqldump（docker compose 使用 MySQL 8.4）
RUN apt-get update && apt-get install -y --no-install-recommends default-mysql-client \
    && rm -rf /var/lib/apt/lists/*

COPY alembic ./alembic
COPY app ./app
COPY alembic.ini .
COPY scripts ./scripts

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
