from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bfmod.installation import InstallationError, resolve_game_root


class InstallationTests(unittest.TestCase):
    def test_explicit_game_root_is_validated_and_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            game_root = Path(temporary_directory) / "Baldr Force Exe"
            (game_root / "Save").mkdir(parents=True)
            for name in ("BaldrForce.exe", "Table.pac", "Exe.pac"):
                (game_root / name).write_bytes(b"fixture")

            self.assertEqual(resolve_game_root(game_root, environment={}), game_root.resolve())

    def test_environment_variable_is_used_when_no_explicit_path_is_given(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            game_root = Path(temporary_directory)
            (game_root / "Save").mkdir()
            for name in ("BaldrForce.exe", "Table.pac", "Exe.pac"):
                (game_root / name).write_bytes(b"fixture")

            resolved = resolve_game_root(
                None,
                environment={"BFMOD_GAME_ROOT": str(game_root)},
                conventional_candidates=(),
            )

            self.assertEqual(resolved, game_root.resolve())

    def test_invalid_explicit_path_reports_missing_game_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            game_root = Path(temporary_directory)

            with self.assertRaisesRegex(InstallationError, "BaldrForce.exe"):
                resolve_game_root(game_root, environment={})

    def test_missing_auto_detected_installation_has_actionable_error(self) -> None:
        with self.assertRaisesRegex(InstallationError, "--game-root"):
            resolve_game_root(None, environment={}, conventional_candidates=())


if __name__ == "__main__":
    unittest.main()
