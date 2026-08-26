"""python -m paper2post 入口。

自动把工程根目录 vendor/（workspace 内的第三方依赖）加入 sys.path，
因此无需额外设置 PYTHONPATH 即可运行。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(os.path.dirname(_ROOT), "vendor")
if os.path.isdir(_VENDOR):
    sys.path.insert(0, _VENDOR)

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
