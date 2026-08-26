"""加载工程配置（config/default.yaml）与环境变量。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# PyInstaller 打包（frozen）时，__file__ 指向临时解包目录；
# 应用数据 / .env 应放到 exe 旁边，便于用户放置密钥与查看产物。
FROZEN = bool(getattr(sys, "frozen", False))
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else PROJECT_ROOT


def load_yaml(path) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _load_dotenv(path: Path) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if path.exists():
        load_dotenv(path)


def _load_all_dotenv() -> None:
    """packaged 时优先读 exe 旁的 .env；其次读工程根 .env。"""
    _load_dotenv(EXE_DIR / ".env")
    _load_dotenv(PROJECT_ROOT / ".env")


def load_settings(config_path: Optional[str] = None) -> Dict[str, Any]:
    """加载 default.yaml；支持 .env 与环境变量覆盖（OPENAI_*）。"""
    # 1) .env -> 环境变量
    _load_all_dotenv()

    # 2) 基础配置
    path = Path(config_path) if config_path else PROJECT_ROOT / "config" / "default.yaml"
    settings = load_yaml(path)

    # 3) 用户设置（Settings 页持久化，覆盖默认值）
    USER_SETTINGS = {"provider", "model", "base_url", "article_type",
                     "article_length", "target_audience", "style", "language"}
    us_settings = load_yaml(PROJECT_ROOT / "data" / "settings.json")
    for k in USER_SETTINGS:
        if k in us_settings and us_settings[k] not in (None, ""):
            settings[k] = us_settings[k]

    # 4) 环境变量覆盖到配置键
    for key, target in [
        ("OPENAI_API_KEY", "api_key"),
        ("OPENAI_BASE_URL", "base_url"),
        ("OPENAI_MODEL", "model"),
    ]:
        val = os.environ.get(key)
        if val:
            settings[target] = val

    settings.setdefault("api_key", os.environ.get("OPENAI_API_KEY"))
    settings.setdefault("base_url", os.environ.get("OPENAI_BASE_URL"))
    settings.setdefault(
        "model",
        os.environ.get("OPENAI_MODEL", settings.get("model", "gpt-4o-mini")),
    )
    return settings
