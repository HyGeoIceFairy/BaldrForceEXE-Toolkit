from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from bfmod.process_memory import WindowsProcessMemoryAdapter


@unittest.skipUnless(os.name == "nt", "Windows-only process memory contract")
class WindowsProcessMemoryAdapterTests(unittest.TestCase):
    def test_reads_project_owned_helper_process_with_read_only_handle(self) -> None:
        helper = Path(__file__).parent / "helpers" / "process_memory_target.py"
        process = subprocess.Popen(
            [sys.executable, str(helper)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            pid_text, address_text = process.stdout.readline().split()
            pid = int(pid_text)
            address = int(address_text)
            adapter = WindowsProcessMemoryAdapter()
            matches = tuple(
                instance
                for instance in adapter.list_processes(Path(sys.executable).name)
                if instance.pid == pid
            )
            self.assertEqual(len(matches), 1)
            connection = adapter.open_process(matches[0])
            try:
                self.assertFalse(connection.writable)
                self.assertEqual(
                    connection.read(address, len(b"BFMOD-WIN32-CONTRACT")),
                    b"BFMOD-WIN32-CONTRACT",
                )
            finally:
                connection.close()
        finally:
            if process.stdin is not None:
                process.stdin.write("exit\n")
                process.stdin.flush()
            process.wait(timeout=10)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()
