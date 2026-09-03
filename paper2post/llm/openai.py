"""OpenAI 兼容 Provider（plain requests 实现）。

历史：2026-09-03 probe 发现 openai SDK + httpx 在 DeepSeek API 上**频繁 hang**——
timeout=20s 也救不回来，单次调用能挂 60s+。改用 `requests` 直接 POST，绕过 httpx 的
连接池 / keepalive 怪行为，flash 4-13s 就能返回。

设计要点：
- 单例 `requests.Session` 全局复用（keep-alive）
- 单次 `requests.post(..., timeout=N)` 严格按 N 秒截断
- 空响应 / 网络错误时 1 次内部 retry（backoff 0.5s）
- vision 用独立临时 Session（避免大图 block 文本请求）
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

from .base import LLMProvider, LLMError


# Vision 模型白名单：只有这些模型名才会被允许调用 analyze_image。
_VISION_MODEL_ALLOWLIST = {
    "deepseek-v4-flash-vision-exp",
    "deepseek-vl", "deepseek-vl2",
    "gpt-4o", "gpt-4o-mini", "gpt-4-vision", "gpt-4-turbo",
    "claude-3-5-sonnet", "claude-3-opus",
}


def _is_vision_model(model: str) -> bool:
    name = (model or "").lower()
    if not name:
        return False
    if name in _VISION_MODEL_ALLOWLIST:
        return True
    return any(kw in name for kw in ("vision", "-vl", "multimodal"))


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _image_to_data_url(path: Union[str, Path]) -> str:
    """读图片文件并编码为 data URL。失败抛 LLMError。"""
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise LLMError(f"image not found: {p}")
    ext = p.suffix.lower()
    mime = _MIME_BY_EXT.get(ext)
    if not mime:
        raise LLMError(f"unsupported image format: {ext}（支持 PNG/JPEG/GIF/WebP）")
    raw = p.read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise LLMError(f"image too large: {len(raw)/1024/1024:.1f}MB (max 32MB)")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


# vision 模型不应该用 response_format 硬约束（经验：会偶发空响应）；
# flash / pro 等纯文本模型则应该用，提升 JSON 稳定性。
def _should_use_json_response_format(model: str) -> bool:
    name = (model or "").lower()
    if not name:
        return False
    if any(kw in name for kw in ("vision", "-vl", "multimodal")):
        return False
    return True


def _is_retriable_error(exc: Exception) -> bool:
    """判断异常是否值得重试。配置错误 / 客户端错误 4xx 都不重试。"""
    name = exc.__class__.__name__
    if name in ("LLMError",):
        msg = str(exc).lower()
        if any(k in msg for k in (
            "not found", "unsupported", "未设置", "not support", "too large",
            "未配置", "invalid api key", "unauthorized",
        )):
            return False
    return True


# 全局 Session（keep-alive），单例复用。
_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        # 不设默认 timeout，强制每个调用显式传
        _SESSION = s
    return _SESSION


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        cfg = self.config or {}
        self.name = str(cfg.get("provider") or "openai")
        api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY")
        base_url = cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL")
        self.model = cfg.get("model") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = float(cfg.get("timeout", 25.0))  # 默认 25s 文本/JSON 调用
        if not self.api_key:
            raise LLMError(
                "OPENAI_API_KEY 未设置。请:\n"
                "  1) copy .env.example .env 并填入密钥;\n"
                "  2) 或使用 --mock 模式跑通链路;\n"
                "  3) 或在 config 中传入 api_key。"
            )

    # ---- helpers ----
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post_chat(
        self,
        payload: Dict[str, Any],
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """直接 POST 到 /chat/completions，返回 message.content。

        失败抛 LLMError（或 requests.RequestException），由调用方决定是否重试。
        """
        url = f"{self.base_url}/chat/completions"
        sess = _get_session()
        t = timeout if timeout is not None else self.timeout
        resp = sess.post(url, json=payload, headers=self._headers(), timeout=t)
        if resp.status_code >= 400:
            # 4xx 不重试（配置/请求问题）
            raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content") or ""

    # ---- main entry ----
    def generate(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        elif _is_vision_model(self.model):
            # vision 模型默认不限制输出 token 容易空响应；强制 600 上限。
            payload["max_tokens"] = 600
        if json_mode and _should_use_json_response_format(self.model):
            payload["response_format"] = {"type": "json_object"}

        # 内部 1 次 retry 兜底：单次 25s × 2 = 50s worst case。
        # 经验：flash 大部分调用 1-15s 就成功；偶尔 1 次空响应 / 慢响应。
        last_err: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                text = self._post_chat(payload, timeout=self.timeout).strip()
                if text:
                    return text
            except LLMError:
                # 4xx / 配置错误：不再重试
                raise
            except Exception as e:
                last_err = e
            if attempt == 1:
                time.sleep(0.5)
        if last_err is not None:
            # 静默失败：把错误留给上层 base.generate_json 打印
            return ""
        return ""

    # ---- vision ----
    def supports_vision(self) -> bool:
        return _is_vision_model(self.model)

    def analyze_image(
        self,
        image_path: Union[str, Path],
        prompt: str,
        *,
        system: str = "你是科研论文图像分析助手。",
        temperature: float = 0.3,
        max_tokens: int = 600,
        timeout: Optional[float] = 8.0,
    ) -> str:
        if not self.supports_vision():
            raise LLMError(
                f"当前模型 {self.model} 不支持 vision。请切换到 deepseek-v4-flash-vision-exp 或其他视觉模型。"
            )
        data_url = _image_to_data_url(image_path)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            text = self._post_chat(payload, timeout=timeout or 8.0).strip()
            return text
        except Exception:
            return ""
