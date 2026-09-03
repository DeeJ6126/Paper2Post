"""OpenAI 兼容 Provider。

默认支持官方 API；通过 base_url 也可接入任意 OpenAI 兼容端点
（代理、本地 vLLM、Ollama 等）。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .base import LLMProvider, LLMError


# Vision 模型白名单：只有这些模型名才会被允许调用 analyze_image。
# DeepSeek 当前只支持 deepseek-v4-flash-vision-exp。
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
    # 启发式：名字里含 vision / vl / multimodal
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
    # 32MB 单图 base64 上限（DeepSeek vision 限制）
    if len(raw) > 32 * 1024 * 1024:
        raise LLMError(f"image too large: {len(raw)/1024/1024:.1f}MB (max 32MB)")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


# vision 模型不应该用 response_format 硬约束（经验：会偶发空响应）；
# flash / pro 等纯文本模型则应该用，提升 JSON 稳定性。
_VISION_MODEL_HINT_KEYWORDS = ("vision", "-vl", "multimodal")


def _should_use_json_response_format(model: str) -> bool:
    """vision 模型不用 response_format；其它模型用。"""
    name = (model or "").lower()
    if not name:
        return False
    if any(kw in name for kw in _VISION_MODEL_HINT_KEYWORDS):
        return False
    return True


def _is_retriable_error(exc: Exception) -> bool:
    """判断异常是否值得重试。配置错误 / 客户端错误 4xx 都不重试。"""
    # 我们的 LLMError：image not found / format / 太大 / 配置错 / model 不支持 vision
    name = exc.__class__.__name__
    if name in ("LLMError",):
        msg = str(exc).lower()
        # 4xx 类配置错误
        if any(k in msg for k in (
            "not found", "unsupported", "未设置", "not support", "too large",
            "未配置", "invalid api key", "unauthorized",
        )):
            return False
    # OpenAI SDK 异常分类
    full_name = f"{exc.__class__.__module__}.{exc.__class__.__name__}"
    if "AuthenticationError" in full_name or "PermissionDeniedError" in full_name:
        return False
    if "BadRequestError" in full_name:  # 400 不重试（prompt 错了重试也没用）
        return False
    if "NotFoundError" in full_name:  # 404 模型不存在
        return False
    return True


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
            "timeout": float(cfg.get("timeout", 15.0)),  # 默认 15s（之前 30s 太长，重复 timeout 浪费 90s）
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
        elif _is_vision_model(self.model):
            # vision 模型默认不限制输出 token 容易空响应；强制 600 上限。
            params["max_tokens"] = 600
        # response_format 区分：vision 模型**不**设（经验：在硬约束下偶发空响应），
        # flash / pro 等纯文本模型设上以提升 JSON 稳定性。
        if json_mode and _should_use_json_response_format(self.model):
            params["response_format"] = {"type": "json_object"}

        # 单次 HTTP 超时 30s（client 配的）。重试策略：
        # - 第一次失败：检查原因。空响应 / JSON 解析失败 → 立刻 fallback（不重试）
        # - 5xx / 网络错 / 限流 → 重试 1 次 + 退避
        # 经验：vision 模型空响应是不可预测的，重试 3 次同样大小 payload 没意义，
        # 上层 base.py 已经做兜底，retries 越快越省时间。
        import time as _t
        last_err: Exception | None = None
        for attempt in range(1, 3):  # 最多 2 次（首次 + 1 次重试）
            try:
                resp = self.client.chat.completions.create(**params)
                text = resp.choices[0].message.content or ""
                if text.strip():
                    return text
                # 空响应：不再重试，让上层 fallback
                last_err = ValueError("empty response")
                break
            except Exception as exc:
                last_err = exc
                if not _is_retriable_error(exc):
                    break
                if attempt < 2:
                    _t.sleep(2.0 * attempt)  # 2s
                    continue
                break
        return ""

    # ---------- vision ----------
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
        """Vision 视觉理解：把本地图片 base64 内联后随 prompt 调一次 LLM。

        要求模型必须在白名单内（vision-capable），否则抛 LLMError。
        返回模型对图的纯文本描述；失败/空响应时返回 ""，由调用方兜底。

        单图 timeout 默认 8s：8 张图 × 8s = 64s worst，配合 2 worker 并行 = 32s worst。
        """
        if not self.supports_vision():
            raise LLMError(
                f"当前模型 {self.model} 不支持 vision。请切换到 deepseek-v4-flash-vision-exp 或其他视觉模型。"
            )
        data_url = _image_to_data_url(image_path)
        # 单图调用使用独立更短 timeout 的 client
        from openai import OpenAI
        one_client = OpenAI(
            api_key=self.client.api_key,
            timeout=float(timeout) if timeout else 8.0,
            max_retries=0,
            base_url=str(self.client.base_url) if self.client.base_url else None,
        )
        params: Dict[str, Any] = {
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
        # 单图只调 1 次，超时立刻返回 ""
        try:
            resp = one_client.chat.completions.create(**params)
            text = resp.choices[0].message.content or ""
            return text.strip() if text.strip() else ""
        except Exception:
            return ""
