"""Anthropic (Claude) Provider。

使用 anthropic SDK。未安装或用不到时无需安装（懒加载）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMError


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        cfg = self.config or {}
        self.api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")
        self.model = (
            cfg.get("model")
            or os.environ.get("ANTHROPIC_MODEL")
            or "claude-3-5-sonnet-latest"
        )
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY 未设置（.env 或 config api_key）。")

        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise LLMError("未安装 anthropic 包：pip install anthropic") from exc

        kwargs: Dict[str, Any] = {"api_key": self.api_key, "timeout": float(cfg.get("timeout", 60.0)), "max_retries": int(cfg.get("max_retries", 1))}
        base_url = cfg.get("base_url") or os.environ.get("ANTHROPIC_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)

    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        # Anthropic 消息协议：system 为顶层参数，无 response_format，靠提示词引导 JSON
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens or 4096,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "".join(parts)
