"""OpenAI 兼容 Provider。

默认支持官方 API；通过 base_url 也可接入任意 OpenAI 兼容端点
（代理、本地 vLLM、Ollama 等）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMError


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        cfg = self.config or {}
        self.name = str(cfg.get("provider") or "openai")
        api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
        base_url = cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL")
        self.model = cfg.get("model") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"

        if not api_key:
            raise LLMError(
                "OPENAI_API_KEY 未设置。请:\n"
                "  1) copy .env.example .env 并填入密钥;\n"
                "  2) 或使用 --mock 模式跑通链路;\n"
                "  3) 或在 config 中传入 api_key。"
            )

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("未安装 openai 包，请 pip install openai") from exc

        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "timeout": float(cfg.get("timeout", 30.0)),
            "max_retries": 0,  # 由本类 generate() 统一做重试，避免双重 retry 把单次延迟放大
        }
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if max_tokens:
            params["max_tokens"] = max_tokens
        # 注：这里**不**设 response_format={"type":"json_object"}。
        # 经验：DeepSeek 的 vision 模型（deepseek-v4-flash-vision-exp）在该硬约束下
        # 偶发返回空字符串，且对部分输入尺寸高度敏感（<=1KB 经常空响应）。
        # 改为依赖 system prompt 中的"输出为合法 JSON"软约束，generate_json 内的
        # parse_json() 会自动剥离 markdown 围栏并截取 { ... } 段。

        # 经验：vision 模型首次冷启动会偶发返回空字符串（包括 chat 长输出场景）。
        # 这里在 provider 层加重试，让所有上层调用（chat / generate_json）都受益。
        # 单次 HTTP 超时 30s（上面 client 配的），重试 3 次 + 0.6/1.2s backoff，
        # 最坏 3 × 30 + 1.8 ≈ 92s 一次调用。
        import time as _t
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                resp = self.client.chat.completions.create(**params)
                text = resp.choices[0].message.content or ""
                if text.strip():
                    return text
                raise ValueError("empty response")
            except Exception as exc:
                last_err = exc
                if attempt < 3:
                    _t.sleep(0.6 * attempt)
                    continue
                break
        # 三次都空或失败，返回空串；上层会兜底
        return ""
