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


def _sanitize_json_text(t: str) -> str:
    """修复 LLM 输出 JSON 里的常见瑕疵，让 raw_decode 救得了。

    - 智能引号 → ASCII "
    - 全角逗号 → ,
    - 尾随逗号 (,}  ,]) → 直接去掉
    - 行注释 // 开头 → 删
    - 控制字符 (\\x00-\\x08, \\x0b-\\x1f 除 \\t\\n) → 删
    - 修复单引号包裹的 key/value（激进；只在 parse 失败时再尝试）
    """
    import re
    # 智能引号 → ASCII 双引号
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    t = t.replace("，", ",")
    # 控制字符
    t = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", t)
    # 行注释（仅在 JSON 字符串外才安全；先做粗处理，由 raw_decode 兜底）
    t = re.sub(r"//[^\n]*", "", t)
    # 尾随逗号
    t = re.sub(r",\s*([}\]])", r"\1", t)
    return t


def parse_json(text: str) -> Any:
    """从 LLM 返回文本中提取 JSON，容忍 markdown 代码块围栏、尾部说明、常见瑕疵。

    经验：vision 模型常常在 JSON 数组后追加 "以下是..." 这种说明文字，
    `json.loads` 会报 "Extra data" 错；改用 `json.JSONDecoder().raw_decode`
    只取第一段合法 JSON，忽略尾部。

    进一步：LLM 偶尔在 JSON 字符串里写未转义的 " 或用智能引号，raw_decode 也救不
    了我们先 sanitize（智能引号替换、尾随逗号、注释、控制字符），再 raw_decode。
    """
    t = (text or "").strip()
    if t.startswith(FENCE):
        t = t.strip(chr(96))
        if t.lower().startswith("json"):
            t = t[4:]
        t = t.strip()
    # 第一次 raw_decode（原文）
    try:
        obj, _ = json.JSONDecoder().raw_decode(t)
        return obj
    except Exception:
        pass
    # 第二次：sanitize 后再试
    t_clean = _sanitize_json_text(t)
    try:
        obj, _ = json.JSONDecoder().raw_decode(t_clean)
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
        raise ValueError("no JSON object/array found in LLM response")
    return json.loads(_sanitize_json_text(t[start : end + 1]))


def generate_json(
    provider: LLMProvider,
    *,
    system: str,
    user: str,
    draft: Any,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    max_retries: int = 1,
    _caller: str = "?",
) -> Any:
    """调用 Provider 请求 JSON；mock 模式或解析失败时回退到 draft。

    max_retries 默认 1：retry 在 provider 层（OpenAIProvider）已经做了 3 次，
    外层再叠 3 次 = 最坏 9 次（277s）。现在外层只重试 1 次（一次性 fallback），
    节省 2/3 时间。
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
                _t.sleep(2.0)  # 给 vision 限流留缓冲
                continue
            break
    import sys as _sys
    _sys.stderr.write(
        f"[generate_json/{_caller}] gave up (provider={getattr(provider, 'name', '?')}, err={last_err}); "
        f"system_len={len(system)}, user_len={len(user)}, raw_head={last_text[:200]!r}\n"
        f"  user_head={user[:200]!r}\n"
    )
    _sys.stderr.flush()
    return draft
