"""供 PyInstaller 以绝对导入收集完整包的桌面启动入口。"""

from __future__ import annotations

import sys

from bfmod.gui import main

if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        from bfmod.release_smoke import run_smoke_test

        raise SystemExit(run_smoke_test())
    raise SystemExit(main())
