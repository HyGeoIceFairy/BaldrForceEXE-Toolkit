from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bfmod import backups as backups_module
from bfmod.backups import (
    create_backup,
    list_backup_files,
    list_backups,
    restore_backup_file,
    verify_backup,
    verify_backup_compatibility,
)
from bfmod.snapshot import SnapshotError, sha256_file


class BackupTests(unittest.TestCase):
    def test_create_list_and_verify_complete_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            (saves / "Config.dat").write_bytes(b"config")
            backup = create_backup(
                saves,
                root / "backups",
                operation="测试应用",
                note="保留这个版本",
            )

            verified = verify_backup(backup.directory)
            listed = list_backups(root / "backups")
            files = list_backup_files(backup.directory)

            self.assertEqual(verified.file_count, 2)
            self.assertEqual(verified.note, "保留这个版本")
            self.assertEqual(listed, (verified,))
            self.assertEqual(
                [item.relative_path for item in files], ["Config.dat", "Save001.dat"]
            )

    def test_rejects_path_traversal_and_tampered_content(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": {}},
            )
            manifest_path = backup.directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["path"] = "../outside.dat"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SnapshotError, "越界"):
                verify_backup(backup.directory)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": {}},
            )
            (backup.directory / "Save" / "Save001.dat").write_bytes(b"evil")
            with self.assertRaises(SnapshotError):
                verify_backup(backup.directory)

    def test_rejects_links_or_reparse_points_inside_backup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            backup = create_backup(saves, root / "backups", operation="test")

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: path.name == "Save001.dat",
            ):
                with self.assertRaisesRegex(SnapshotError, "链接|重解析"):
                    verify_backup(backup.directory)

    def test_rejects_link_or_reparse_point_backup_roots(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            backup_root = root / "backups"
            backup_root.mkdir()

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: path == backup_root,
            ):
                with self.assertRaisesRegex(SnapshotError, "备份根目录|链接|重解析"):
                    create_backup(saves, backup_root, operation="test")

    def test_verify_rejects_linked_backup_directory_save_root_or_manifest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            backup = create_backup(saves, root / "backups", operation="test")
            guarded_paths = (
                backup.directory,
                backup.directory / "Save",
                backup.directory / "manifest.json",
            )

            for guarded_path in guarded_paths:
                with self.subTest(path=guarded_path.name):
                    with patch(
                        "bfmod.snapshot._is_link_or_reparse_point",
                        side_effect=lambda path, guarded=guarded_path: path == guarded,
                    ):
                        with self.assertRaisesRegex(SnapshotError, "链接|重解析"):
                            verify_backup(backup.directory)

    def test_rejects_backup_root_inside_or_around_save_directory(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "game" / "Save"
            saves.mkdir(parents=True)
            (saves / "Save001.dat").write_bytes(b"slot")
            with self.assertRaisesRegex(SnapshotError, "Save"):
                create_backup(saves, saves / "backups", operation="bad")
            with self.assertRaisesRegex(SnapshotError, "Save"):
                create_backup(saves, root, operation="bad")

    def test_restore_requires_an_exact_file_from_the_verified_manifest(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            saves = root / "game" / "Save"
            saves.mkdir(parents=True)
            destination = saves / "Save001.dat"
            destination.write_bytes(b"current")
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": {}},
            )
            outside = root / "outside.dat"
            outside.write_bytes(b"must not be restored")

            with self.assertRaisesRegex(SnapshotError, "清单"):
                restore_backup_file(
                    backup.directory,
                    str(outside),
                destination,
                expected_destination_sha256=sha256_file(destination),
                expected_candidate_sha256="0" * 64,
                transaction_backup_root=root / "transactions",
                expected_fingerprints={},
                validator=None,
            )

            self.assertEqual(destination.read_bytes(), b"current")

    def test_restore_compatibility_requires_matching_installation_fingerprints(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            fingerprints = {
                "BaldrForce.exe": "A" * 64,
                "Table.pac": "B" * 64,
            }
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": fingerprints},
            )

            verified = verify_backup_compatibility(backup.directory, fingerprints)
            self.assertEqual(verified.directory, backup.directory)
            with self.assertRaisesRegex(SnapshotError, "游戏版本"):
                verify_backup_compatibility(
                    backup.directory,
                    {**fingerprints, "Table.pac": "C" * 64},
                )

            legacy = create_backup(saves, root / "legacy", operation="test")
            with self.assertRaisesRegex(SnapshotError, "版本指纹"):
                verify_backup_compatibility(legacy.directory, fingerprints)

    def test_listing_and_compatibility_use_the_manifest_they_verified(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "Save"
            saves.mkdir()
            (saves / "Save001.dat").write_bytes(b"slot")
            fingerprints = {"BaldrForce.exe": "A" * 64}
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": fingerprints},
            )

            with patch(
                "bfmod.backups._load_manifest",
                wraps=backups_module._load_manifest,
            ) as load_manifest:
                self.assertEqual(len(list_backup_files(backup.directory)), 1)
                self.assertEqual(load_manifest.call_count, 1)
            with patch(
                "bfmod.backups._load_manifest",
                wraps=backups_module._load_manifest,
            ) as load_manifest:
                verify_backup_compatibility(backup.directory, fingerprints)
                self.assertEqual(load_manifest.call_count, 1)

    def test_restore_rechecks_compatibility_in_the_same_verified_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "game" / "Save"
            saves.mkdir(parents=True)
            destination = saves / "Save001.dat"
            destination.write_bytes(b"current")
            fingerprints = {"BaldrForce.exe": "A" * 64}
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": fingerprints},
            )

            restore_backup_file(
                backup.directory,
                destination.name,
                destination,
                expected_destination_sha256=sha256_file(destination),
                expected_candidate_sha256=sha256_file(
                    backup.directory / "Save" / destination.name
                ),
                transaction_backup_root=root / "transactions",
                expected_fingerprints=fingerprints,
                process_checker=lambda _names: (),
            )
            self.assertEqual(destination.read_bytes(), b"current")
            with self.assertRaisesRegex(SnapshotError, "游戏版本"):
                restore_backup_file(
                    backup.directory,
                    destination.name,
                    destination,
                    expected_destination_sha256=sha256_file(destination),
                    expected_candidate_sha256=sha256_file(
                        backup.directory / "Save" / destination.name
                    ),
                    transaction_backup_root=root / "transactions-2",
                    expected_fingerprints={"BaldrForce.exe": "B" * 64},
                    process_checker=lambda _names: (),
                )

    def test_restore_requires_the_candidate_confirmed_in_the_preview(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            saves = root / "game" / "Save"
            saves.mkdir(parents=True)
            destination = saves / "Save001.dat"
            destination.write_bytes(b"current")
            backup = create_backup(
                saves,
                root / "backups",
                operation="test",
                metadata={"installation_fingerprints": {}},
            )
            confirmed_hash = sha256_file(backup.directory / "Save" / destination.name)

            with self.assertRaisesRegex(SnapshotError, "预览|重新确认"):
                restore_backup_file(
                    backup.directory,
                    destination.name,
                    destination,
                    expected_destination_sha256=sha256_file(destination),
                    expected_candidate_sha256="F" * 64,
                    transaction_backup_root=root / "transactions",
                    expected_fingerprints={},
                )

            self.assertNotEqual(confirmed_hash, "F" * 64)
            self.assertEqual(destination.read_bytes(), b"current")


if __name__ == "__main__":
    unittest.main()
