from __future__ import annotations

import os
import sys
from ctypes import addressof, c_ubyte

SENTINEL = b"BFMOD-WIN32-CONTRACT"
BUFFER = (c_ubyte * len(SENTINEL)).from_buffer_copy(SENTINEL)


def main() -> int:
    print(f"{os.getpid()} {addressof(BUFFER)}", flush=True)
    for line in sys.stdin:
        if line.strip() == "exit":
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
