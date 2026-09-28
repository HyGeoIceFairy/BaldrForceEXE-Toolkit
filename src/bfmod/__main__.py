"""允许通过 ``python -m bfmod`` 启动桌面修改器。"""

from __future__ import annotations

from .gui import main

if __name__ == "__main__":
    raise SystemExit(main())
