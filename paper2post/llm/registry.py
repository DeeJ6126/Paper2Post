"""LLM Provider 注册表：统一 OpenAI 兼容 与 原生协议 Provider。"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .base import LLMProvider, LLMError

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# 文本优先：vision 在 6KB+ 输入下空响应。flash 是当前 Pipeline 真正稳定的模型。
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_MODELS = [
    DEEPSEEK_DEFAULT_MODEL,
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
]

# 走 OpenAI 兼容协议（base_url + model）即可接入的 Provider
# env_key: 该服务对应的 API Key 环境变量名
OPENAI_COMPATIBLE: Dict[str, Dict[str, str]] = {
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini", "env_key": "OPENAI_API_KEY"},
    "deepseek": {
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_DEFAULT_MODEL,
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "env_key": "DASHSCOPE_API_KEY"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "env_key": "MOONSHOT_API_KEY"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.1-70b-versatile", "env_key": "GROQ_API_KEY"},
    "mistral": {"base_url": "https://api.mistral.ai/v1", "model": "mistral-small-latest", "env_key": "MISTRAL_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-4o-mini", "env_key": "OPENROUTER_API_KEY"},
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "llama3.1", "env_key": "OLLAMA_API_KEY"},
    "vllm": {"base_url": "http://localhost:8000/v1", "model": "", "env_key": "VLLM_API_KEY"},
}

# 原生协议 Provider（独立实现）
SPECIAL_PROVIDERS = {"anthropic", "gemini"}

ALIASES = {
    "deepseek": "DeepSeek",
    "qwen": "Qwen (DashScope)",
    "moonshot": "Moonshot (Kimi)",
    "groq": "Groq",
    "mistral": "Mistral",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (本地)",
    "vllm": "vLLM (本地)",
    "anthropic": "Anthropic (Claude)",
    "gemini": "Google Gemini",
    "openai": "OpenAI",
}


def all_provider_names() -> list:
    return list(ALIASES.keys())


def _resolve_key(cfg: dict, alias: dict, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env_key = alias.get("env_key") or "OPENAI_API_KEY"
    return os.environ.get(env_key) or cfg.get("api_key") or ""


def build(
    settings: Dict[str, Any],
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMProvider:
    """按 provider 名称构建对应 Provider。"""
    name = (provider_name or settings.get("provider", "openai") or "openai").lower()
    cfg: Dict[str, Any] = dict(settings)

    if name == "mock":
        from .mock import MockProvider

        return MockProvider()

    if name in OPENAI_COMPATIBLE:
        from .openai import OpenAIProvider

        alias = OPENAI_COMPATIBLE[name]
        cfg["provider"] = name
        cfg["model"] = model or cfg.get("model") or alias.get("model") or "gpt-4o-mini"
        cfg["base_url"] = base_url or alias.get("base_url") or cfg.get("base_url") or ""
        cfg["api_key"] = _resolve_key(cfg, alias, api_key)
        return OpenAIProvider(cfg)

    if name == "anthropic":
        from .anthropic import AnthropicProvider

        cfg["model"] = model or cfg.get("model") or os.environ.get("ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest"
        cfg["api_key"] = api_key or os.environ.get("ANTHROPIC_API_KEY") or cfg.get("api_key")
        cfg["base_url"] = base_url or cfg.get("base_url")
        return AnthropicProvider(cfg)

    if name == "gemini":
        from .gemini import GeminiProvider

        cfg["model"] = model or cfg.get("model") or os.environ.get("GEMINI_MODEL") or "gemini-2.0-flash"
        cfg["api_key"] = api_key or os.environ.get("GEMINI_API_KEY") or cfg.get("api_key")
        cfg["base_url"] = base_url or cfg.get("base_url")
        return GeminiProvider(cfg)

    raise LLMError(f"未知 LLM Provider：{name}。可选：{', '.join(all_provider_names())} 或 mock")
