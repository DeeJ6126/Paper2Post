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
    """从 LLM 返回文本中提取 JSON，容忍 markdown 代码块围栏、尾部说明。

    经验：vision 模型常常在 JSON 数组后追加 "以下是..." 这种说明文字，
    `json.loads` 会报 "Extra data" 错；改用 `json.JSONDecoder().raw_decode`
    只取第一段合法 JSON，忽略尾部。
    """
    t = (text or "").strip()
    if t.startswith(FENCE):
        t = t.strip(chr(96))
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    # 先尝试 raw_decode：能解析首段 JSON 并忽略尾部
    try:
        obj, _ = json.JSONDecoder().raw_decode(t)
        return obj
    except Exception:
        pass
    # 回退：找最外层 { ... } 或 [ ... ]
    start_brace = t.find("{")
    start_bracket = t.find("[")
    if start_brace == -1:
        start = start_bracket
    elif start_bracket == -1:
        start = start_brace
    else:
        start = min(start_brace, start_bracket)
    end_brace = t.rfind("}")
    end_bracket = t.rfind("]")
    end = max(end_brace, end_bracket)
    if start == -1 or end == -1 or end <= start:
        raise
    return json.loads(t[start : end + 1])


def generate_json(
    provider: LLMProvider,
    *,
    system: str,
    user: str,
    draft: Any,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
) -> Any:
    """调用 Provider 请求 JSON；mock 模式或解析失败时回退到 draft。

    经验：DeepSeek vision 模型（deepseek-v4-flash-vision-exp）首次冷启动调用
    偶发返回空字符串，重试一次通常就能拿到正常响应。max_retries 控制重试次数，
    默认 3 次（首次 + 2 次重试）。空响应与 JSON 解析失败都触发重试。
    """
    if getattr(provider, "is_mock", False):
        return draft
    import time as _t
    last_text = ""
    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            text = provider.generate(
                system=system,
                user=user,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            last_text = text or ""
            if not last_text.strip():
                raise ValueError("empty response")
            return parse_json(last_text)
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                _t.sleep(0.6 * attempt)  # 0.6s, 1.2s backoff
                continue
            break
    import sys as _sys
    _sys.stderr.write(
        f"[generate_json] gave up after {max_retries} attempts "
        f"(provider={getattr(provider, 'name', '?')}, err={last_err}); "
        f"system_len={len(system)}, user_len={len(user)}, raw_head={last_text[:200]!r}\n"
    )
    _sys.stderr.flush()
    return draft
