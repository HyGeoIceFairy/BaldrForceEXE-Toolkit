from __future__ import annotations

import argparse
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from bfmod import diagnostics
from bfmod.pac import LzssResult, PacEntry
from bfmod.versioning import InstallationIdentity
from bfmod.workspace import GameWorkspace, SlotLoadFailure


class DiagnosticsTests(unittest.TestCase):
    def test_inspect_reports_all_failed_slots_instead_of_aborting(self) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            save_directory = game_root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"config")
            (game_root / "Table.pac").write_bytes(b"table")
            (game_root / "Exe.pac").write_bytes(b"exe")
            failed_path = save_directory / "Save001.dat"
            failed_path.write_bytes(b"broken")
            workspace = GameWorkspace(
                game_root=game_root,
                reference_table=game_root / "BFWT.htm",
                identity=InstallationIdentity(
                    name="test",
                    fingerprints={},
                    is_known=False,
                    read_supported=True,
                    write_supported=False,
                    reasons=("unknown",),
                ),
                catalog=(),
                force_crash_catalog=(),
                waz=object(),  # type: ignore[arg-type]
                slots=(),
                slot_failures=(SlotLoadFailure(1, failed_path, "损坏"),),
            )
            decoded_config = SimpleNamespace(
                encoded_size=6,
                decoded=b"decoded",
                leaf_count=1,
                consumed_bits=8,
            )
            pac_entry = PacEntry("EquipmentInfo.DAT", 0, 11, 11)
            pac_extract = LzssResult(b"description", 11, 11)
            pac_archive = SimpleNamespace(
                version=100,
                find=lambda _name: pac_entry,
                extract=lambda _entry: pac_extract,
            )

            with (
                patch.object(diagnostics, "load_workspace", return_value=workspace) as load,
                patch.object(
                    diagnostics,
                    "decode_container",
                    return_value=decoded_config,
                ),
                patch.object(diagnostics, "PacArchive", return_value=pac_archive),
            ):
                report = diagnostics._inspect(game_root)

            load.assert_called_once_with(
                game_root,
                None,
                allow_no_healthy_slots=True,
            )
            self.assertEqual(report["slots"], [])
            self.assertEqual(
                report["slot_failures"],
                [{"file": "Save001.dat", "number": 1, "message": "损坏"}],
            )
            capabilities = report["capabilities"]
            self.assertIsInstance(capabilities, list)
            realtime_connection = next(
                item for item in capabilities if item["id"] == "realtime_connection"
            )
            self.assertEqual(realtime_connection["state"], "unavailable")
            self.assertTrue(realtime_connection["reasons"])

    def test_inspect_keeps_other_sections_when_config_and_one_pac_are_broken(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            save_directory = game_root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"broken config")
            (game_root / "Table.pac").write_bytes(b"broken table")
            (game_root / "Exe.pac").write_bytes(b"healthy exe")
            workspace = self._empty_workspace(game_root)
            pac_entry = PacEntry("EquipmentInfo.DAT", 0, 11, 11)
            pac_extract = LzssResult(b"description", 11, 11)
            healthy_archive = SimpleNamespace(
                version=100,
                find=lambda _name: pac_entry,
                extract=lambda _entry: pac_extract,
            )

            def open_pac(data: bytes) -> object:
                if data == b"broken table":
                    raise ValueError("PAC 已损坏")
                return healthy_archive

            with (
                patch.object(diagnostics, "load_workspace", return_value=workspace),
                patch.object(
                    diagnostics,
                    "decode_container",
                    side_effect=ValueError("Config 已损坏"),
                ),
                patch.object(diagnostics, "PacArchive", side_effect=open_pac),
            ):
                report = diagnostics._inspect(game_root)

            self.assertEqual(report["weapon_count"], 0)
            self.assertEqual(report["config"]["status"], "error")
            self.assertIn("Config 已损坏", report["config"]["error"])
            equipment_info = report["equipment_info"]
            self.assertEqual(equipment_info[0]["archive"], "Table.pac")
            self.assertEqual(equipment_info[0]["status"], "error")
            self.assertIn("PAC 已损坏", equipment_info[0]["error"])
            self.assertEqual(equipment_info[1]["archive"], "Exe.pac")
            self.assertEqual(equipment_info[1]["status"], "ok")
            self.assertEqual(
                equipment_info[1]["decoded_sha256"],
                hashlib.sha256(b"description").hexdigest().upper(),
            )

    def test_inspect_reports_missing_pac_entry_as_a_partition_error(self) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            (game_root / "Table.pac").write_bytes(b"table")
            archive = SimpleNamespace(
                version=100,
                find=lambda _name: (_ for _ in ()).throw(
                    KeyError("PAC 中不存在 EquipmentInfo.DAT")
                ),
            )

            with patch.object(diagnostics, "PacArchive", return_value=archive):
                report = diagnostics._inspect_pac(game_root, "Table.pac")

            self.assertEqual(report["archive"], "Table.pac")
            self.assertEqual(report["status"], "error")
            self.assertIn("PAC 中不存在 EquipmentInfo.DAT", report["error"])

    def test_inspect_reports_broken_waz_without_losing_independent_sections(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            save_directory = game_root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"config")
            (game_root / "Table.pac").write_bytes(b"table")
            (game_root / "Exe.pac").write_bytes(b"exe")
            waz_directory = game_root / "Dat" / "Waza"
            waz_directory.mkdir(parents=True)
            (waz_directory / "TOORU.WAZ").write_bytes(b"broken waz")
            identity = self._identity()
            decoded_config = SimpleNamespace(
                encoded_size=6,
                decoded=b"decoded",
                leaf_count=1,
                consumed_bits=8,
            )
            pac_entry = PacEntry("EquipmentInfo.DAT", 0, 11, 11)
            pac_extract = LzssResult(b"description", 11, 11)
            pac_archive = SimpleNamespace(
                version=100,
                find=lambda _name: pac_entry,
                extract=lambda _entry: pac_extract,
            )

            with (
                patch.object(
                    diagnostics,
                    "load_workspace",
                    side_effect=ValueError("TOORU.WAZ 已损坏"),
                ),
                patch.object(
                    diagnostics,
                    "parse_waz",
                    side_effect=ValueError("WAZ 记录不完整"),
                ),
                patch.object(
                    diagnostics,
                    "identify_installation",
                    return_value=identity,
                ),
                patch.object(
                    diagnostics,
                    "decode_container",
                    return_value=decoded_config,
                ),
                patch.object(diagnostics, "PacArchive", return_value=pac_archive),
            ):
                report = diagnostics._inspect(game_root)

            self.assertEqual(report["workspace"]["status"], "error")
            self.assertIn("TOORU.WAZ 已损坏", report["workspace"]["error"])
            self.assertEqual(report["waz"]["status"], "error")
            self.assertIn("WAZ 记录不完整", report["waz"]["error"])
            self.assertEqual(report["installation"]["name"], "test")
            self.assertEqual(report["config"]["status"], "ok")
            self.assertTrue(
                all(item["status"] == "ok" for item in report["equipment_info"])
            )
            self.assertEqual(report["slots"], [])
            self.assertEqual(report["slot_failures"], [])

    def test_inspect_reports_realtime_profile_failure_without_aborting(self) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            save_directory = game_root / "Save"
            save_directory.mkdir()
            (save_directory / "Config.dat").write_bytes(b"config")
            (game_root / "Table.pac").write_bytes(b"table")
            (game_root / "Exe.pac").write_bytes(b"exe")
            workspace = self._empty_workspace(game_root)
            decoded_config = SimpleNamespace(
                encoded_size=6,
                decoded=b"decoded",
                leaf_count=1,
                consumed_bits=8,
            )
            pac_entry = PacEntry("EquipmentInfo.DAT", 0, 11, 11)
            pac_extract = LzssResult(b"description", 11, 11)
            pac_archive = SimpleNamespace(
                version=100,
                find=lambda _name: pac_entry,
                extract=lambda _entry: pac_extract,
            )

            with (
                patch.object(diagnostics, "load_workspace", return_value=workspace),
                patch.object(
                    diagnostics,
                    "select_realtime_profile",
                    side_effect=OSError("无法读取指令指纹"),
                ),
                patch.object(
                    diagnostics,
                    "decode_container",
                    return_value=decoded_config,
                ),
                patch.object(diagnostics, "PacArchive", return_value=pac_archive),
            ):
                report = diagnostics._inspect(game_root)

            self.assertEqual(report["realtime_profile"]["status"], "error")
            self.assertIn("无法读取指令指纹", report["realtime_profile"]["error"])
            self.assertEqual(report["config"]["status"], "ok")

    def test_inspect_reports_one_file_hash_failure_without_aborting(self) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            save_directory = game_root / "Save"
            save_directory.mkdir()
            config_path = save_directory / "Config.dat"
            config_path.write_bytes(b"config")
            (game_root / "Table.pac").write_bytes(b"table")
            (game_root / "Exe.pac").write_bytes(b"exe")
            workspace = self._empty_workspace(game_root)
            decoded_config = SimpleNamespace(
                encoded_size=6,
                decoded=b"decoded",
                leaf_count=1,
                consumed_bits=8,
            )
            pac_entry = PacEntry("EquipmentInfo.DAT", 0, 11, 11)
            pac_extract = LzssResult(b"description", 11, 11)
            pac_archive = SimpleNamespace(
                version=100,
                find=lambda _name: pac_entry,
                extract=lambda _entry: pac_extract,
            )

            def file_record(path: Path, root: Path) -> dict[str, object]:
                if path == config_path:
                    raise OSError("哈希读取失败")
                return {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                }

            with (
                patch.object(diagnostics, "load_workspace", return_value=workspace),
                patch.object(
                    diagnostics,
                    "decode_container",
                    return_value=decoded_config,
                ),
                patch.object(diagnostics, "PacArchive", return_value=pac_archive),
                patch.object(diagnostics, "_file_record", side_effect=file_record),
            ):
                report = diagnostics._inspect(game_root)

            self.assertTrue(report["files"])
            self.assertEqual(report["file_failures"][0]["path"], "Save/Config.dat")
            self.assertIn("哈希读取失败", report["file_failures"][0]["error"])

    @staticmethod
    def _identity() -> InstallationIdentity:
        return InstallationIdentity(
            name="test",
            fingerprints={},
            is_known=False,
            read_supported=True,
            write_supported=False,
            reasons=("unknown",),
        )

    @classmethod
    def _empty_workspace(cls, game_root: Path) -> GameWorkspace:
        return GameWorkspace(
            game_root=game_root,
            reference_table=game_root / "BFWT.htm",
            identity=cls._identity(),
            catalog=(),
            force_crash_catalog=(),
            waz=object(),  # type: ignore[arg-type]
            slots=(),
            slot_failures=(),
        )

    def test_inspect_uses_workspace_fallback_when_reference_table_is_omitted(self) -> None:
        with TemporaryDirectory() as temporary:
            game_root = Path(temporary)
            args = argparse.Namespace(game_root=str(game_root), reference_table=None)
            workspace = GameWorkspace(
                game_root=game_root,
                reference_table=game_root / "BFWT.htm",
                identity=InstallationIdentity(
                    name="test",
                    fingerprints={},
                    is_known=True,
                    read_supported=True,
                    write_supported=True,
                    reasons=("test",),
                ),
                catalog=(),
                force_crash_catalog=(),
                waz=object(),  # type: ignore[arg-type]
                slots=(),
                slot_failures=(),
            )
            report = {"schema": 1, "workspace": "shared"}

            with (
                patch.object(diagnostics, "resolve_game_root", return_value=game_root),
                patch.object(diagnostics, "load_workspace", return_value=workspace),
                patch.object(diagnostics, "_inspect", return_value=report) as inspect,
                patch("builtins.print") as print_output,
            ):
                result = diagnostics.command_inspect(args)

            self.assertEqual(result, 0)
            inspect.assert_called_once_with(game_root, None)
            print_output.assert_called_once_with(
                json.dumps(report, ensure_ascii=False, indent=2)
            )

    def test_parser_does_not_require_a_local_reference_table(self) -> None:
        args = diagnostics.build_parser().parse_args(
            ["inspect", "--game-root", "C:/Game/Baldr Force Exe"]
        )
        self.assertIsNone(args.reference_table)

    def test_force_crash_validation_command_is_explicit_and_read_only(self) -> None:
        args = diagnostics.build_parser().parse_args(
            [
                "validate-force-crash",
                "--source",
                "source.dat",
                "--candidate",
                "candidate.dat",
                "--slot-name",
                "Save002.dat",
                "--output",
                "record.json",
            ]
        )
        report = object()
        written = Path("record.json").resolve()

        with (
            patch.object(
                diagnostics,
                "_resolve_existing_file",
                side_effect=(Path("source.dat"), Path("candidate.dat")),
            ),
            patch.object(
                diagnostics,
                "inspect_force_crash_candidate",
                return_value=report,
            ) as inspect,
            patch.object(diagnostics, "_assert_output_under_project") as assert_output,
            patch.object(
                diagnostics,
                "write_force_crash_validation_record",
                return_value=written,
            ) as write_record,
            patch("builtins.print") as print_output,
        ):
            result = diagnostics.command_validate_force_crash(args)

        self.assertEqual(result, 0)
        inspect.assert_called_once_with(Path("source.dat"), Path("candidate.dat"))
        assert_output.assert_called_once_with(written)
        write_record.assert_called_once_with(
            report,
            written,
            slot_name="Save002.dat",
        )
        print_output.assert_called_once_with(written)

    def test_force_crash_validation_rejects_output_outside_project(self) -> None:
        args = argparse.Namespace(
            source="source.dat",
            candidate="candidate.dat",
            slot_name="Save002.dat",
            output="outside.json",
        )

        with (
            patch.object(
                diagnostics,
                "_resolve_existing_file",
                side_effect=(Path("source.dat"), Path("candidate.dat")),
            ),
            patch.object(
                diagnostics,
                "inspect_force_crash_candidate",
                return_value=object(),
            ),
            patch.object(
                diagnostics,
                "_assert_output_under_project",
                side_effect=ValueError("输出越界"),
            ),
            patch.object(diagnostics, "write_force_crash_validation_record") as write_record,
        ):
            with self.assertRaisesRegex(ValueError, "输出越界"):
                diagnostics.command_validate_force_crash(args)

        write_record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
