#!/usr/bin/env python
"""Paper2Post 顶层启动脚本。

    python paper2post.py <论文.pdf> [--mock] [--article-type ...]

会优先加载工程根目录下的 vendor/（workspace 内的第三方依赖），
随后委托给 paper2post.cli 完成实际工作。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)

_VENDOR = os.path.join(_ROOT, "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from paper2post.cli import main

if __name__ == "__main__":
    sys.exit(main())
