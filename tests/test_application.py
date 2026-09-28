from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from struct import pack, pack_into
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bfmod import application, storage
from bfmod.application import (
    ApplicationError,
    apply_force_crash_session,
    create_manual_backup,
)
from bfmod.force_crash import (
    FORCE_CRASH_COUNT,
    FORCE_CRASH_RECORD_START,
    ForceCrashDefinition,
    ForceCrashSession,
)
from bfmod.huffman import encode_container
from bfmod.save_slot import decode_save_slot, replace_save_slot_attachment
from bfmod.storage import ConcurrentModificationError
from bfmod.versioning import InstallationIdentity
from bfmod.waz import WazArchive
from bfmod.weapon_progress import (
    PROGRESS_BLOCK_SIZE,
    PROGRESS_RECORD_OFFSET,
    PROGRESS_RECORD_SIZE,
    decode_weapon_progress_attachment,
)
from bfmod.workspace import GameWorkspace, LoadedSlot


def _save_with_force_crash_progress() -> bytes:
    decoded = bytearray(PROGRESS_BLOCK_SIZE)
    for record_index in range(213):
        offset = PROGRESS_RECORD_OFFSET + record_index * PROGRESS_RECORD_SIZE
        decoded[offset] = 5 if record_index == FORCE_CRASH_RECORD_START else 1
        pack_into("<i", decoded, offset + 0x04, record_index)
    progress_attachment = encode_container(bytes(decoded))[8:]
    main_container = encode_container(b"main payload")
    main_payload = main_container[8:]
    attachments = (b"first", progress_attachment, b"third", b"fourth")
    sizes = (len(main_payload), *(len(item) for item in attachments))
    return pack("<7I", 100, len(b"main payload"), *sizes) + main_payload + b"".join(
        attachments
    )


def _identity(*, write_supported: bool = True) -> InstallationIdentity:
    return InstallationIdentity(
        name="test installation",
        fingerprints={"BaldrForce.exe": "A" * 64},
        is_known=write_supported,
        read_supported=True,
        write_supported=write_supported,
        reasons=("test identity",),
    )


def _workspace(game_root: Path) -> tuple[GameWorkspace, LoadedSlot, ForceCrashSession]:
    source = game_root / "Save" / "Save001.dat"
    raw = source.read_bytes()
    slot = decode_save_slot(raw)
    progress = decode_weapon_progress_attachment(
        slot.weapon_progress_attachment,
        record_count=213,
    )
    loaded = LoadedSlot(
        number=1,
        path=source,
        raw=raw,
        sha256=hashlib.sha256(raw).hexdigest().upper(),
        slot=slot,
        progress=progress,
        is_special=False,
    )
    definitions = tuple(
        ForceCrashDefinition(
            number=number,
            record_index=FORCE_CRASH_RECORD_START + number,
            name_ja=f"Force Crash {number}",
            description_ja=f"说明 {number}",
            progress=progress.records[FORCE_CRASH_RECORD_START + number],
        )
        for number in range(FORCE_CRASH_COUNT)
    )
    workspace = GameWorkspace(
        game_root=game_root,
        reference_table=game_root / "BFWT.htm",
        identity=_identity(),
        catalog=(),
        force_crash_catalog=(),
        waz=WazArchive(0, (), b""),
        slots=(loaded,),
        slot_failures=(),
    )
    return workspace, loaded, ForceCrashSession(definitions)


def _commit_without_process_check(*args: object, **kwargs: object) -> object:
    kwargs["process_checker"] = lambda _names: ()
    return storage.commit_file(*args, **kwargs)  # type: ignore[arg-type]


class ForceCrashApplicationTests(unittest.TestCase):
    def test_apply_commits_only_selected_states_and_creates_full_backup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            original = _save_with_force_crash_progress()
            (save_directory / "Save001.dat").write_bytes(original)
            (save_directory / "Config.dat").write_bytes(b"config sentinel")
            workspace, loaded, session = _workspace(game_root)
            session.stage_unlock((1, 20))

            with (
                patch.object(application, "identify_installation", return_value=_identity()),
                patch.object(application, "running_processes", return_value=()),
                patch.object(
                    application,
                    "commit_file",
                    side_effect=_commit_without_process_check,
                ),
            ):
                result = apply_force_crash_session(
                    workspace,
                    loaded,
                    session,
                    backup_root=root / "backups",
                    note="isolated test",
                )

            committed = decode_save_slot(loaded.path.read_bytes())
            after = decode_weapon_progress_attachment(
                committed.weapon_progress_attachment,
                record_count=213,
            )
            self.assertEqual(after.records[193].development_state, 5)
            self.assertEqual(after.records[212].development_state, 5)
            self.assertEqual(after.records[194].raw, loaded.progress.records[194].raw)
            self.assertEqual(committed.main_payload, loaded.slot.main_payload)
            self.assertEqual(committed.attachments[0], loaded.slot.attachments[0])
            self.assertEqual(committed.attachments[2:], loaded.slot.attachments[2:])
            self.assertEqual(
                (result.backup.directory / "Save" / loaded.path.name).read_bytes(),
                original,
            )
            self.assertEqual(
                (result.backup.directory / "Save" / "Config.dat").read_bytes(),
                b"config sentinel",
            )
            manifest = json.loads(
                (result.backup.directory / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["metadata"]["operation"], "应用 Save001.dat Force Crash 解锁")
            self.assertEqual(
                [change["record_index"] for change in manifest["metadata"]["changes"]],
                [193, 212],
            )

    def test_apply_rejects_unknown_version_and_running_game_before_backup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            (save_directory / "Save001.dat").write_bytes(
                _save_with_force_crash_progress()
            )
            workspace, loaded, session = _workspace(game_root)
            session.stage_unlock((1,))

            with (
                patch.object(
                    application,
                    "identify_installation",
                    return_value=_identity(write_supported=False),
                ),
                patch.object(application, "create_backup") as create_backup,
            ):
                with self.assertRaisesRegex(ApplicationError, "版本"):
                    apply_force_crash_session(
                        workspace,
                        loaded,
                        session,
                        backup_root=root / "unknown-version-backups",
                    )
                create_backup.assert_not_called()

            with (
                patch.object(application, "identify_installation", return_value=_identity()),
                patch.object(
                    application,
                    "running_processes",
                    return_value=("BaldrForce.exe",),
                ),
                patch.object(application, "create_backup") as create_backup,
            ):
                with self.assertRaisesRegex(ApplicationError, "退出游戏"):
                    apply_force_crash_session(
                        workspace,
                        loaded,
                        session,
                        backup_root=root / "running-game-backups",
                    )
                create_backup.assert_not_called()

    def test_apply_rejects_candidate_outside_attachment_whitelist(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            (save_directory / "Save001.dat").write_bytes(
                _save_with_force_crash_progress()
            )
            workspace, loaded, session = _workspace(game_root)
            session.stage_unlock((1,))
            valid_candidate = session.render_save(loaded.slot)
            malicious_slot = decode_save_slot(valid_candidate)
            invalid_candidate = replace_save_slot_attachment(
                malicious_slot,
                0,
                b"unexpected change",
            )

            with (
                patch.object(application, "identify_installation", return_value=_identity()),
                patch.object(application, "running_processes", return_value=()),
                patch.object(session, "render_save", return_value=invalid_candidate),
                patch.object(application, "create_backup") as create_backup,
            ):
                with self.assertRaisesRegex(ApplicationError, "非武器附属块"):
                    apply_force_crash_session(
                        workspace,
                        loaded,
                        session,
                        backup_root=root / "backups",
                    )
                create_backup.assert_not_called()

    def test_apply_detects_source_conflict_without_overwriting_external_data(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            (save_directory / "Save001.dat").write_bytes(
                _save_with_force_crash_progress()
            )
            workspace, loaded, session = _workspace(game_root)
            session.stage_unlock((1,))
            external_data = b"externally changed"
            loaded.path.write_bytes(external_data)

            with (
                patch.object(application, "identify_installation", return_value=_identity()),
                patch.object(application, "running_processes", return_value=()),
                patch.object(
                    application,
                    "commit_file",
                    side_effect=_commit_without_process_check,
                ),
            ):
                with self.assertRaises(ConcurrentModificationError):
                    apply_force_crash_session(
                        workspace,
                        loaded,
                        session,
                        backup_root=root / "backups",
                    )

            self.assertEqual(loaded.path.read_bytes(), external_data)


class ManualBackupApplicationTests(unittest.TestCase):
    def test_manual_backup_rechecks_current_version_and_records_fingerprints(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            (save_directory / "Save001.dat").write_bytes(
                _save_with_force_crash_progress()
            )
            workspace, _loaded, _session = _workspace(game_root)

            current_identity = InstallationIdentity(
                name="current test installation",
                fingerprints={"BaldrForce.exe": "B" * 64},
                is_known=True,
                read_supported=True,
                write_supported=True,
                reasons=("current identity",),
            )
            with (
                patch.object(
                    application,
                    "identify_installation",
                    return_value=current_identity,
                ) as identify,
                patch.object(application, "running_processes", return_value=()),
            ):
                backup = create_manual_backup(
                    workspace,
                    backup_root=root / "backups",
                    note="current-version test",
                )

            identify.assert_called_once_with(game_root)
            manifest = json.loads(
                (backup.directory / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["metadata"]["installation_fingerprints"],
                current_identity.fingerprints,
            )

    def test_manual_backup_allows_unknown_current_version_and_records_it(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            game_root = root / "game"
            save_directory = game_root / "Save"
            save_directory.mkdir(parents=True)
            (save_directory / "Save001.dat").write_bytes(
                _save_with_force_crash_progress()
            )
            workspace, _loaded, _session = _workspace(game_root)
            current_identity = _identity(write_supported=False)

            with (
                patch.object(
                    application,
                    "identify_installation",
                    return_value=current_identity,
                ),
                patch.object(application, "running_processes", return_value=()),
            ):
                backup = create_manual_backup(
                    workspace,
                    backup_root=root / "backups",
                )

            manifest = json.loads(
                (backup.directory / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                manifest["metadata"]["installation_fingerprints"],
                current_identity.fingerprints,
            )


if __name__ == "__main__":
    unittest.main()
