#!/usr/bin/env python
"""Paper2Post 桌面端应用。

默认：打开**原生 Tkinter 窗口**（无浏览器、无 WebView2/.NET 依赖，任何环境都可用）。
可选：用 WebView2 的原生网页窗口（UI 更丰富，但需 WebView2/.NET）：

    python desktop.py              # 原生 Tkinter 窗口
    python desktop.py --webview    # 尝试 pywebview；不可用则回退到 Tkinter 窗口
"""

import argparse
import os
import socket
import sys
import threading

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_tk() -> int:
    from tkapp import run_app

    run_app()
    return 0


def _run_webview() -> int:
    try:
        import webview
        from webapp.server import create_server

        host = "127.0.0.1"
        port = _free_port()
        httpd = create_server(host, port)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        url = f"http://{host}:{port}/"
        print("Paper2Post (WebView) 已启动:", url)
        webview.create_window(
            "Paper2Post", url, width=1280, height=900, min_size=(960, 640)
        )
        webview.start()
        httpd.shutdown()
        httpd.server_close()
        return 0
    except Exception as exc:  # noqa
        print("WebView 不可用，回退到原生 Tkinter 窗口:", exc)
        return _run_tk()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="desktop", description="Paper2Post 桌面端")
    parser.add_argument(
        "--tk",
        action="store_true",
        help="强制使用原生 Tkinter 窗口（而非 WebView2 网页窗口）",
    )
    args = parser.parse_args(argv)
    if args.tk:
        return _run_tk()
    # 默认：优先用 WebView2 原生窗口加载与网页端一致的 UI；不可用则回退 Tkinter
    return _run_webview()


if __name__ == "__main__":
    sys.exit(main())
