#!/usr/bin/env python
"""启动 Paper2Post Web App。

    python run_web.py [--port 8000] [--no-browser]

自动把工程根目录 vendor/ 加入 sys.path。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from webapp.server import main

if __name__ == "__main__":
    sys.exit(main())