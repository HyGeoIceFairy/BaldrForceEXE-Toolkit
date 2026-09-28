from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bfmod.snapshot import SnapshotError, create_verified_snapshot, ensure_plain_path_chain


class SnapshotTests(unittest.TestCase):
    def test_publishes_snapshot_only_after_verifying_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            save_directory = root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"configuration")
            (save_directory / "Save001.dat").write_bytes(b"save-slot")
            target = root / "baseline" / "snapshot-1"

            manifest = create_verified_snapshot(
                save_directory,
                target,
                metadata={"game_root": "C:/Game/Baldr Force Exe"},
            )

            self.assertTrue(target.is_dir())
            self.assertFalse(target.with_name(".snapshot-1.incomplete").exists())
            self.assertEqual((target / "Save" / "Config.dat").read_bytes(), b"configuration")
            written_manifest = json.loads((target / "manifest.json").read_text("utf-8"))
            self.assertEqual(written_manifest, manifest)
            self.assertEqual(written_manifest["state"], "verified")
            self.assertEqual(
                [record["path"] for record in written_manifest["files"]],
                ["Config.dat", "Save001.dat"],
            )

    def test_does_not_publish_when_source_changes_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            save_directory = root / "Save"
            save_directory.mkdir()
            source = save_directory / "Config.dat"
            source.write_bytes(b"before")
            target = root / "baseline" / "snapshot-2"

            from bfmod import snapshot

            real_copy = snapshot.shutil.copy2

            def copy_then_change(source_path: Path, target_path: Path) -> str:
                result = real_copy(source_path, target_path)
                source.write_bytes(b"after")
                return str(result)

            with patch("bfmod.snapshot.shutil.copy2", side_effect=copy_then_change):
                with self.assertRaisesRegex(SnapshotError, "复制期间发生变化"):
                    create_verified_snapshot(save_directory, target)

            self.assertFalse(target.exists())
            self.assertTrue(target.with_name(".snapshot-2.incomplete").is_dir())

    def test_refuses_to_overwrite_existing_snapshot_or_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            save_directory = root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"data")
            target = root / "baseline" / "snapshot-3"
            target.mkdir(parents=True)

            with self.assertRaisesRegex(FileExistsError, "已存在"):
                create_verified_snapshot(save_directory, target)

    def test_rejects_links_and_reparse_points_in_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            save_directory = root / "Save"
            save_directory.mkdir()
            (save_directory / "linked.dat").write_bytes(b"not actually copied")
            target = root / "baseline" / "snapshot-linked"

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: path.name == "linked.dat",
            ):
                with self.assertRaisesRegex(SnapshotError, "链接|重解析"):
                    create_verified_snapshot(save_directory, target)

            self.assertFalse(target.exists())

    def test_rejects_source_root_that_is_a_link_or_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            save_directory = root / "Save"
            save_directory.mkdir()
            (save_directory / "Save001.dat").write_bytes(b"slot")
            target = root / "baseline" / "snapshot-linked-root"

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: path == save_directory,
            ):
                with self.assertRaisesRegex(SnapshotError, "根目录|链接|重解析"):
                    create_verified_snapshot(save_directory, target)

            self.assertFalse(target.exists())

    def test_rejects_an_intermediate_link_or_reparse_point_before_resolving(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            boundary = root / "selected-backup-root"
            nested = boundary / "snapshots"

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: path == boundary,
            ):
                with self.assertRaisesRegex(SnapshotError, "链接|重解析"):
                    ensure_plain_path_chain(
                        nested,
                        description="备份根目录",
                        allow_missing=True,
                    )


if __name__ == "__main__":
    unittest.main()
