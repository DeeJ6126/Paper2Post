#!/usr/bin/env python
"""用 PyInstaller 打包 Paper2Post 为单文件桌面程序。

前置：pip install -r requirements.txt pyinstaller
用法：python build_exe.py
产物：dist/Paper2Post(.exe)

说明：
  - 默认打包「OpenAI 兼容 + Mock」所需的依赖（openai / pymupdf / pydantic 等）。
  - 若已安装 anthropic、google-genai，会一并打包以便支持 Claude / Gemini。
  - 打包产物在运行时把结果写到 exe 旁的 outputs_web/，密钥放 exe 旁的 .env。
"""

import importlib.util
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SEP = ";" if os.name == "nt" else ":"

_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def main():
    # 让 PyInstaller 分析阶段能同时看到 vendor 与 site-packages 里的依赖
    vendor = os.path.join(_ROOT, "vendor")
    paths = [vendor] if os.path.isdir(vendor) else []
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([p for p in paths + ([existing] if existing else [])])

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        "Paper2Post",
        "--onefile",
        "--windowed",
        "--clean",
        "--noconfirm",
    ]
    # 让分析阶段能解析 vendor
    if paths:
        cmd += ["--paths", vendor]

    for src, dst in [
        ("webapp/static", "webapp/static"),
        ("prompts", "prompts"),
        ("config", "config"),
    ]:
        cmd += ["--add-data", src + _SEP + dst]

    # 收集需要整包（含二进制 .pyd/.dll 与子模块）的依赖
    collect = ["pymupdf", "openai", "pydantic", "yaml", "dotenv", "anyio"]
    for pkg in ["anthropic", "google", "google.genai", "httpx2", "httpcore2", "jiter", "pydantic_core"]:
        # 判断是否已安装（google 命名空间按 google 判断）
        base = pkg.split(".")[0]
        if _installed(pkg) or _installed(base):
            collect.append(pkg)
    for pkg in collect:
        try:
            cmd += ["--collect-all", pkg]
        except Exception:
            pass

    # 隐藏导入（懒加载可能静态分析不到）
    for hidden in ["pymupdf", "openai", "anthropic", "google.genai", "google", "yaml", "dotenv", "pydantic"]:
        if _installed(hidden) or _installed(hidden.split(".")[0]):
            cmd += ["--hidden-import", hidden]

    # 排除本应用不需要、但 base 环境可能被误收的重型库（会拖慢启动并导致运行期崩溃）
    for mod in [
        "numpy", "pandas", "scipy", "matplotlib", "tkinter", "IPython",
        "pytest", "pyarrow", "PyQt5", "PySide2", "PySide6", "cv2",
        "PIL", "flask", "django", "torch", "tensorflow",
    ]:
        cmd += ["--exclude-module", mod]

    cmd.append(os.path.join(_ROOT, "desktop.py"))

    print("PyInstaller 命令:")
    print("  " + " ".join(cmd))
    print("\n开始构建……")
    subprocess.check_call(cmd, env=env)
    out = "dist/Paper2Post" + (".exe" if os.name == "nt" else "")
    print("\n构建完成 -> " + os.path.join(_ROOT, out))


if __name__ == "__main__":
    main()
