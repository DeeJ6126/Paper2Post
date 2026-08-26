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
            "timeout": float(cfg.get("timeout", 60.0)),
            "max_retries": int(cfg.get("max_retries", 1)),
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
        if json_mode:
            params["response_format"] = {"type": "json_object"}

        resp = self.client.chat.completions.create(**params)
        return resp.choices[0].message.content or ""
