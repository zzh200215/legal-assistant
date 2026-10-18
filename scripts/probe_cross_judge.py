"""探针：现有凭据能不能驱动一个「与 glm-5.2 不同家族」的裁判模型。

背景：诊断文档 §8 定的交叉裁判是 deepseek-v4.1-flash，但仓库里没有任何 deepseek 凭据
（`.env` 只有 LLM_*=sensenova/glm-5.2 与 EMBEDDING_*=dashscope/text-embedding-v3）。
本探针检查 dashscope 的兼容模式端点是否能用同一个 key 调 chat 模型，从而零新增凭据
拿到一个真正的交叉模型裁判。

用法：python scripts/probe_cross_judge.py [model ...]
"""

from __future__ import annotations

import sys

import httpx

from app.core.config import get_settings

DEFAULT_MODELS = ["qwen-flash", "qwen-turbo", "qwen-plus", "qwen3-max"]


def main() -> int:
    settings = get_settings()
    key = settings.EMBEDDING_API_KEY
    base = settings.EMBEDDING_API_BASE_URL.rstrip("/")
    models = sys.argv[1:] or DEFAULT_MODELS
    print(f"base={base} key_present={bool(key)}")
    ok = 0
    for model in models:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "回复一个字：好"}],
            "max_tokens": 8,
        }
        try:
            resp = httpx.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload,
                timeout=40,
            )
            body = resp.text.replace("\n", " ")[:220]
            print(f"{model} -> {resp.status_code} {body}")
            if resp.status_code == 200:
                ok += 1
        except Exception as exc:  # noqa: BLE001 - 探针要如实报告任何失败
            print(f"{model} -> ERR {type(exc).__name__}: {str(exc)[:140]}")
    print(f"usable_models={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
