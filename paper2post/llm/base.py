"""LLM Provider 抽象基类与 JSON 辅助函数。"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class LLMError(RuntimeError):
    """LLM 配置或调用错误。"""


class LLMProvider(ABC):
    """统一 Provider 接口，避免被单一 API 锁死。"""

    name: str = "base"
    is_mock: bool = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    @abstractmethod
    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        """生成文字。json_mode=True 时提示模型返回可解析的 JSON。"""

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        return self.generate(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=False,
        )

    def analyze_image(self, image_path: str, prompt: str) -> str:
        """图像理解；默认不支持。"""
        raise NotImplementedError(f"{self.name} 不支持图像理解")


FENCE = chr(96) * 3  # triple backtick markdown fence


def parse_json(text: str) -> Any:
    """从 LLM 返回文本中提取 JSON，容忍 markdown 代码块围栏。"""
    t = (text or "").strip()
    if t.startswith(FENCE):
        t = t.strip(chr(96))
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1:
        t = t[start : end + 1]
    return json.loads(t)


def generate_json(
    provider: LLMProvider,
    *,
    system: str,
    user: str,
    draft: Any,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> Any:
    """调用 Provider 请求 JSON；mock 模式或解析失败时回退到 draft。"""
    if getattr(provider, "is_mock", False):
        return draft
    text = provider.generate(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=True,
    )
    try:
        return parse_json(text)
    except Exception:
        return draft
