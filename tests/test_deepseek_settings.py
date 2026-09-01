"""DeepSeek-only settings regression tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper2post.llm import registry
from webapp import server


@contextmanager
def isolated_server_settings():
    old_root = server._ROOT
    old_settings_path = server.SETTINGS_PATH
    old_load_settings = server.load_settings
    old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        server._ROOT = root
        server.SETTINGS_PATH = root / "data" / "settings.json"
        server.load_settings = lambda: {
            "provider": "deepseek",
            "model": registry.DEEPSEEK_DEFAULT_MODEL,
            "base_url": registry.DEEPSEEK_BASE_URL,
        }
        try:
            yield root
        finally:
            server._ROOT = old_root
            server.SETTINGS_PATH = old_settings_path
            server.load_settings = old_load_settings
            if old_key is not None:
                os.environ["DEEPSEEK_API_KEY"] = old_key
            else:
                os.environ.pop("DEEPSEEK_API_KEY", None)


def test_catalog_and_default():
    assert registry.DEEPSEEK_DEFAULT_MODEL == "deepseek-v4-flash"
    assert registry.DEEPSEEK_BASE_URL == "https://api.deepseek.com"
    assert registry.DEEPSEEK_MODELS == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash-vision-exp",
    ]


def test_models_info_is_deepseek_only_and_secret_free():
    with isolated_server_settings():
        info = server.get_models_info()
        assert info["provider"] == "deepseek"
        assert info["model"] == "deepseek-v4-flash"
        assert info["models"] == registry.DEEPSEEK_MODELS
        assert info["allow_custom_model"] is True
        assert info["has_api_key"] is False
        assert "api_key" not in info
        assert "providers" not in info
        assert "base_url" not in info


def test_save_models_writes_key_without_returning_it_and_preserves_empty_key():
    with isolated_server_settings() as root:
        secret = "ds-test-secret"
        result = server.save_models(
            {"provider": "openai", "base_url": "https://invalid.example", "model": "deepseek-v4-pro", "api_key": secret}
        )
        env_path = root / ".env"
        assert env_path.exists()
        assert env_path.read_text(encoding="utf-8") == f"DEEPSEEK_API_KEY={secret}\n"
        assert result["provider"] == "deepseek"
        assert result["model"] == "deepseek-v4-pro"
        assert "api_key" not in result

        preserved = server.save_models({"model": "deepseek-v4-flash", "api_key": ""})
        assert env_path.read_text(encoding="utf-8") == f"DEEPSEEK_API_KEY={secret}\n"
        assert preserved["model"] == "deepseek-v4-flash"

        settings = json.loads(server.SETTINGS_PATH.read_text(encoding="utf-8"))
        assert settings == {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
        }


def test_save_models_rejects_empty_model():
    with isolated_server_settings():
        try:
            server.save_models({"model": "  "})
        except ValueError as exc:
            assert "model" in str(exc).lower()
        else:
            raise AssertionError("empty model must be rejected")


def test_frontend_uses_deepseek_model_selector_without_provider_controls():
    source = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'provider: "deepseek"' in source
    assert 'model: "deepseek-v4-flash"' in source
    assert "__custom__" in source
    assert "var PROVIDERS" not in source
    assert 't("服务商", "Provider")' not in source
    assert 't("Base URL", "Base URL")' not in source
    assert '"&provider="' not in source


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"[ok] {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
