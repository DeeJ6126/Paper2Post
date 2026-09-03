"""Agent 公共基类与辅助函数。"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from paper2post.prompts import Prompts
from paper2post.llm.base import LLMProvider


class BaseAgent:
    def __init__(
        self,
        llm: LLMProvider,
        prompts: Prompts,
        config: Optional[dict] = None,
    ):
        self.llm = llm
        self.prompts = prompts
        self.config = config or {}

    # ---- helpers ----
    def dump(self, obj: Any) -> str:
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump()
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def temperature(self) -> float:
        return float(self.config.get("temperature", 0.7))

    def max_tokens(self) -> Optional[int]:
        # vision 模型默认 800 tokens（vision 在 >800 输出容易空响应）；
        # flash / pro 等纯文本模型默认 4096。
        try:
            from paper2post.llm.openai import _is_vision_model
            default = 800 if _is_vision_model(getattr(self.llm, "model", "")) else 4096
        except Exception:
            default = 4096
        mt = self.config.get("max_tokens", default)
        return int(mt) if mt else None

    @staticmethod
    def first_sentence(text: str) -> str:
        m = re.match(r"(.+?[。.!?])", (text or "").strip())
        return m.group(1) if m else ""
