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
    """读取 .env -> os.environ。

    使用 override=True：.env 中的值覆盖同名 shell 环境变量，
    这样用户更新 .env 即可生效，不必重启服务（实际请求时 load_settings 会重读）。
    若 python-dotenv 未安装，则用内置解析回退到环境变量。
    """
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=True)
        return
    except ImportError:
        pass
    # 回退：手写解析 KEY=VALUE（忽略注释与引号），不覆盖已有值
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


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
    #    DeepSeek 是当前默认 provider，必须把 DEEPSEEK_API_KEY 也传进去，
    #    否则 settings.api_key 始终为 None，registry 只能靠 os.environ 兜底。
    for key, target in [
        ("DEEPSEEK_API_KEY", "api_key"),
        ("OPENAI_API_KEY", "api_key"),
        ("OPENAI_BASE_URL", "base_url"),
        ("OPENAI_MODEL", "model"),
        ("DEEPSEEK_BASE_URL", "base_url"),
    ]:
        val = os.environ.get(key)
        if val and not settings.get(target):
            settings[target] = val

    settings.setdefault("api_key", os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    settings.setdefault("base_url", os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    settings.setdefault(
        "model",
        os.environ.get("DEEPSEEK_MODEL") or os.environ.get("OPENAI_MODEL") or settings.get("model", "gpt-4o-mini"),
    )
    return settings
