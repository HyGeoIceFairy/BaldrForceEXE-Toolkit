"""供 PyInstaller 以绝对导入收集完整包的桌面启动入口。"""

from __future__ import annotations

from bfmod.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
