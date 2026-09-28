from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bfmod.workspace import _load_slots


class WorkspaceSlotIsolationTests(unittest.TestCase):
    def test_load_slots_keeps_valid_slots_and_reports_invalid_ones(self) -> None:
        with TemporaryDirectory() as temporary:
            save_directory = Path(temporary)
            for name in ("Save001.dat", "Save002.dat", "notes.txt"):
                (save_directory / name).write_bytes(b"sample")
            healthy_slot = Mock(number=1)

            def load(path: Path, number: int) -> object:
                if number == 2:
                    raise ValueError("成长块损坏")
                return healthy_slot

            with patch("bfmod.workspace._load_slot", side_effect=load):
                slots, failures = _load_slots(save_directory)

        self.assertEqual(slots, (healthy_slot,))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].number, 2)
        self.assertEqual(failures[0].path.name, "Save002.dat")
        self.assertIn("成长块损坏", failures[0].message)


if __name__ == "__main__":
    unittest.main()
