"""Google Gemini Provider。

使用 google-genai SDK（from google import genai）。懒加载。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMError


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        cfg = self.config or {}
        self.api_key = cfg.get("api_key") or os.environ.get("GEMINI_API_KEY")
        self.model = cfg.get("model") or os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY 未设置（.env 或 config api_key）。")

        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise LLMError("未安装 google-genai 包：pip install google-genai") from exc

        kwargs: Dict[str, Any] = {"api_key": self.api_key, "http_options": genai.types.HttpOptions(timeout=float(cfg.get("timeout", 60.0)))}
        base_url = cfg.get("base_url") or os.environ.get("GEMINI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self.genai = genai
        self.client = genai.Client(**kwargs)

    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        GenConfig = self.genai.types.GenerateContentConfig
        cfg: Dict[str, Any] = {
            "system_instruction": system,
            "temperature": temperature,
        }
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        if max_tokens:
            cfg["max_output_tokens"] = max_tokens
        resp = self.client.models.generate_content(
            model=self.model,
            contents=user,
            config=GenConfig(**cfg),
        )
        # candidates[0].content.parts[0].text
        try:
            return resp.text or ""
        except Exception:
            # 兼容不同版本字段
            cands = getattr(resp, "candidates", None) or []
            if cands:
                return cands[0].content.parts[0].text or ""
            return ""
