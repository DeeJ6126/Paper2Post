"""Mock Provider：无需 API Key 即可跑通全链路。

各 Agent 在 mock 模式下通过 generate_json 的 draft 参数产出默认对象，
因此即使不调用真实模型也能得到合法的结构化输出。
"""

from __future__ import annotations

from typing import Optional

from .base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"
    is_mock = True

    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        # mock 不真正调用模型；Agent 依赖 draft 兜底
        return "{}"
