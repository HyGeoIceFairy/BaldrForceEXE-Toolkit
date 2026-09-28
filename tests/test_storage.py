from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bfmod import storage as storage_module
from bfmod.snapshot import SnapshotError
from bfmod.storage import (
    ConcurrentModificationError,
    GameRunningError,
    StorageError,
    commit_file,
    recover_transactions,
)


class StorageTests(unittest.TestCase):
    def test_commit_backs_up_then_replaces_and_verifies(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old save")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old save").hexdigest().upper()

            result = commit_file(
                source,
                b"new save",
                expected_source_sha256=expected,
                backup_root=backup_root,
                game_process_names=("BaldrForce.exe",),
                process_checker=lambda _names: (),
                validator=lambda data: (
                    None
                    if data == b"new save"
                    else (_ for _ in ()).throw(ValueError())
                ),
            )

            self.assertEqual(source.read_bytes(), b"new save")
            self.assertEqual(result.original_sha256, expected)
            self.assertEqual(result.final_sha256, hashlib.sha256(b"new save").hexdigest().upper())
            self.assertEqual((result.backup_directory / source.name).read_bytes(), b"old save")
            self.assertFalse(result.transaction_path.exists())
            self.assertTrue(
                (result.backup_directory / "transaction.resolved.json").exists()
            )

    def test_refuses_external_change_game_running_and_overlapping_backup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old save")

            with self.assertRaises(ConcurrentModificationError):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256="0" * 64,
                    backup_root=root / "backups",
                    process_checker=lambda _names: (),
                )

    def test_rechecks_game_process_immediately_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"original")
            checks = iter(((), ("BaldrForce.exe",)))

            with self.assertRaisesRegex(GameRunningError, "游戏仍在运行"):
                commit_file(
                    source,
                    b"candidate",
                    expected_source_sha256=hashlib.sha256(b"original")
                    .hexdigest()
                    .upper(),
                    backup_root=root / "backups",
                    process_checker=lambda _names: next(checks),
                )

            self.assertEqual(source.read_bytes(), b"original")
            with self.assertRaises(GameRunningError):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=hashlib.sha256(b"old save").hexdigest().upper(),
                    backup_root=root / "backups",
                    process_checker=lambda _names: ("BaldrForce.exe",),
                )
            with self.assertRaisesRegex(StorageError, "存档目录"):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=hashlib.sha256(b"old save").hexdigest().upper(),
                    backup_root=source.parent / "backups",
                    process_checker=lambda _names: (),
                )

    def test_validation_failure_does_not_touch_source(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old save")
            expected = hashlib.sha256(b"old save").hexdigest().upper()
            with self.assertRaisesRegex(StorageError, "候选存档验证失败"):
                commit_file(
                    source,
                    b"broken",
                    expected_source_sha256=expected,
                    backup_root=root / "backups",
                    process_checker=lambda _names: (),
                    validator=lambda _data: (_ for _ in ()).throw(ValueError("bad")),
                )
            self.assertEqual(source.read_bytes(), b"old save")

    def test_replace_failure_preserves_backup_and_transaction_for_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old save")
            expected = hashlib.sha256(b"old save").hexdigest().upper()
            with patch("bfmod.storage.os.replace", side_effect=OSError("locked")):
                with self.assertRaisesRegex(StorageError, "替换"):
                    commit_file(
                        source,
                        b"new save",
                        expected_source_sha256=expected,
                        backup_root=root / "backups",
                        process_checker=lambda _names: (),
                    )
            self.assertEqual(source.read_bytes(), b"old save")
            self.assertTrue(any((root / "backups").glob("*/transaction.json")))
            recovery = recover_transactions(
                root / "backups",
                save_directory=source.parent,
            )
            self.assertEqual([item.state for item in recovery], ["not_committed"])
            self.assertFalse(any((root / "backups").glob("*/transaction.json")))
            self.assertTrue(any((root / "backups").glob("*/transaction.resolved.json")))

    def test_recovery_resumes_an_atomically_claimed_transaction(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "game" / "Save" / "Save001.dat"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"old")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / source.name
            backup.write_bytes(b"old")
            original_hash = hashlib.sha256(b"old").hexdigest().upper()
            resolving_path = transaction_directory / "transaction.resolving.json"
            resolving_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source.resolve()),
                        "backup": str(backup.resolve()),
                        "original_sha256": original_hash,
                        "candidate_sha256": hashlib.sha256(b"new").hexdigest().upper(),
                    }
                ),
                encoding="utf-8",
            )

            recovery = recover_transactions(
                root / "transactions",
                save_directory=source.parent,
            )

            self.assertEqual([item.state for item in recovery], ["not_committed"])
            self.assertFalse(resolving_path.exists())
            self.assertTrue(
                (transaction_directory / "transaction.resolved.json").exists()
            )

    def test_resolved_audit_record_does_not_block_later_legitimate_changes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "game" / "Save" / "Save001.dat"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"third state")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / source.name
            backup.write_bytes(b"old")
            resolved_path = transaction_directory / "transaction.resolved.json"
            resolved_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source.resolve()),
                        "backup": str(backup.resolve()),
                        "original_sha256": hashlib.sha256(b"old")
                        .hexdigest()
                        .upper(),
                        "candidate_sha256": hashlib.sha256(b"new")
                        .hexdigest()
                        .upper(),
                    }
                ),
                encoding="utf-8",
            )

            recovery = recover_transactions(
                root / "transactions",
                save_directory=source.parent,
            )

            self.assertEqual(recovery, ())
            self.assertTrue(resolved_path.exists())

    def test_recovery_refuses_unknown_source_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            source = root / "game" / "Save" / "Save001.dat"
            source.parent.mkdir(parents=True)
            backup = transaction_directory / "Save001.dat"
            source.write_bytes(b"third state")
            backup.write_bytes(b"old")
            original_hash = hashlib.sha256(b"old").hexdigest().upper()
            candidate_hash = hashlib.sha256(b"new").hexdigest().upper()
            (transaction_directory / "transaction.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source),
                        "backup": str(backup),
                        "original_sha256": original_hash,
                        "candidate_sha256": candidate_hash,
                    }
                ),
                encoding="utf-8",
            )

            recovery = recover_transactions(
                root / "transactions",
                save_directory=source.parent,
            )

            self.assertEqual(recovery[0].state, "needs_attention")
            self.assertTrue((transaction_directory / "transaction.json").exists())

    def test_commit_rejects_link_or_reparse_point_source_and_backup_boundaries(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "Save"
            save_directory.mkdir()
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old").hexdigest().upper()

            for unsafe_path in (save_directory, source, backup_root):
                with self.subTest(path=unsafe_path.name):
                    with patch(
                        "bfmod.snapshot._is_link_or_reparse_point",
                        side_effect=lambda path, unsafe=unsafe_path: path == unsafe,
                    ):
                        with self.assertRaisesRegex(StorageError, "链接|重解析"):
                            commit_file(
                                source,
                                b"new",
                                expected_source_sha256=expected,
                                backup_root=backup_root,
                                process_checker=lambda _names: (),
                            )

    def test_commit_rechecks_transaction_backup_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "Save"
            save_directory.mkdir()
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old").hexdigest().upper()
            checks = 0

            def tamper_after_backup(_names: object) -> tuple[str, ...]:
                nonlocal checks
                checks += 1
                if checks == 2:
                    backup_file = next(backup_root.glob("*/Save001.dat"))
                    backup_file.write_bytes(b"tampered")
                return ()

            with self.assertRaisesRegex(StorageError, "事务备份|备份哈希"):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=expected,
                    backup_root=backup_root,
                    process_checker=tamper_after_backup,
                )

            self.assertEqual(source.read_bytes(), b"old")
            self.assertTrue(any(backup_root.glob("*/transaction.json")))

    def test_commit_rechecks_backup_path_chain_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "Save"
            save_directory.mkdir()
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old").hexdigest().upper()
            final_check = False

            def mark_final_check(_names: object) -> tuple[str, ...]:
                nonlocal final_check
                if backup_root.exists():
                    final_check = True
                return ()

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: final_check and path == backup_root,
            ):
                with self.assertRaisesRegex(StorageError, "事务备份|链接|重解析"):
                    commit_file(
                        source,
                        b"new",
                        expected_source_sha256=expected,
                        backup_root=backup_root,
                        process_checker=mark_final_check,
                    )

            self.assertEqual(source.read_bytes(), b"old")
            self.assertTrue(any(backup_root.glob("*/transaction.json")))

    def test_commit_rechecks_original_source_path_chain_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "Save"
            save_directory.mkdir()
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old").hexdigest().upper()
            final_check = False

            def mark_final_check(_names: object) -> tuple[str, ...]:
                nonlocal final_check
                if backup_root.exists():
                    final_check = True
                return ()

            with patch(
                "bfmod.snapshot._is_link_or_reparse_point",
                side_effect=lambda path: final_check and path == save_directory,
            ):
                with self.assertRaisesRegex(StorageError, "存档目录|链接|重解析"):
                    commit_file(
                        source,
                        b"new",
                        expected_source_sha256=expected,
                        backup_root=backup_root,
                        process_checker=mark_final_check,
                    )

            self.assertEqual(source.read_bytes(), b"old")

    def test_commit_rechecks_source_hash_after_the_final_process_check(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old")
            expected = hashlib.sha256(b"old").hexdigest().upper()
            checks = 0

            def mutate_during_final_check(_names: object) -> tuple[str, ...]:
                nonlocal checks
                checks += 1
                if checks == 2:
                    source.write_bytes(b"external change")
                return ()

            with self.assertRaisesRegex(ConcurrentModificationError, "提交期间"):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=expected,
                    backup_root=root / "backups",
                    process_checker=mutate_during_final_check,
                )

            self.assertEqual(source.read_bytes(), b"external change")

    def test_commit_rechecks_temporary_candidate_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old")
            expected = hashlib.sha256(b"old").hexdigest().upper()
            checks = 0

            def tamper_temporary_candidate(_names: object) -> tuple[str, ...]:
                nonlocal checks
                checks += 1
                if checks == 2:
                    temporary_path = next(source.parent.glob(".*.bfmod-*.tmp"))
                    temporary_path.write_bytes(b"different candidate")
                return ()

            with self.assertRaisesRegex(StorageError, "候选|临时"):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=expected,
                    backup_root=root / "backups",
                    process_checker=tamper_temporary_candidate,
                )

            self.assertEqual(source.read_bytes(), b"old")

    def test_commit_rechecks_transaction_manifest_before_replacing(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "Save" / "Save001.dat"
            source.parent.mkdir()
            source.write_bytes(b"old")
            backup_root = root / "backups"
            expected = hashlib.sha256(b"old").hexdigest().upper()
            checks = 0

            def tamper_transaction_manifest(_names: object) -> tuple[str, ...]:
                nonlocal checks
                checks += 1
                if checks == 2:
                    transaction_path = next(backup_root.glob("*/transaction.json"))
                    transaction_path.write_text("{}", encoding="utf-8")
                return ()

            with self.assertRaisesRegex(StorageError, "事务清单"):
                commit_file(
                    source,
                    b"new",
                    expected_source_sha256=expected,
                    backup_root=backup_root,
                    process_checker=tamper_transaction_manifest,
                )

            self.assertEqual(source.read_bytes(), b"old")
            self.assertTrue(any(backup_root.glob("*/transaction.json")))

    def test_recovery_rejects_manifest_paths_outside_declared_boundaries(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "game" / "Save"
            save_directory.mkdir(parents=True)
            legitimate_source = save_directory / "Save001.dat"
            legitimate_source.write_bytes(b"old")
            outside_source = root / "outside.dat"
            outside_source.write_bytes(b"old")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / "Save001.dat"
            backup.write_bytes(b"old")
            original_hash = hashlib.sha256(b"old").hexdigest().upper()
            transaction_path = transaction_directory / "transaction.json"
            transaction_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(outside_source),
                        "backup": str(backup),
                        "original_sha256": original_hash,
                        "candidate_sha256": hashlib.sha256(b"new").hexdigest().upper(),
                    }
                ),
                encoding="utf-8",
            )

            recovery = recover_transactions(
                root / "transactions",
                save_directory=save_directory,
            )

            self.assertEqual(recovery[0].state, "needs_attention")
            self.assertTrue(transaction_path.exists())
            self.assertEqual(outside_source.read_bytes(), b"old")

    def test_recovery_rechecks_source_before_removing_transaction(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "game" / "Save"
            save_directory.mkdir(parents=True)
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / source.name
            backup.write_bytes(b"old")
            original_hash = hashlib.sha256(b"old").hexdigest().upper()
            transaction_path = transaction_directory / "transaction.json"
            transaction_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source.resolve()),
                        "backup": str(backup.resolve()),
                        "original_sha256": original_hash,
                        "candidate_sha256": hashlib.sha256(b"new").hexdigest().upper(),
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "bfmod.storage.sha256_file",
                side_effect=(original_hash, original_hash, "F" * 64),
            ):
                recovery = recover_transactions(
                    root / "transactions",
                    save_directory=save_directory,
                )

            self.assertEqual(recovery[0].state, "needs_attention")
            self.assertTrue(transaction_path.exists())

    def test_recovery_keeps_transaction_when_unlink_preconditions_change(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "game" / "Save"
            save_directory.mkdir(parents=True)
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / source.name
            backup.write_bytes(b"old")
            original_hash = hashlib.sha256(b"old").hexdigest().upper()
            transaction_path = transaction_directory / "transaction.json"
            transaction_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source.resolve()),
                        "backup": str(backup.resolve()),
                        "original_sha256": original_hash,
                        "candidate_sha256": hashlib.sha256(b"new").hexdigest().upper(),
                    }
                ),
                encoding="utf-8",
            )

            with patch(
                "bfmod.storage._resolve_confirmed_transaction",
                side_effect=ValueError("事务目录在解除前发生变化"),
            ):
                recovery = recover_transactions(
                    root / "transactions",
                    save_directory=save_directory,
                )

            self.assertEqual(recovery[0].state, "needs_attention")
            self.assertTrue(transaction_path.exists())

    def test_recovery_rechecks_all_state_after_atomic_claim(self) -> None:
        mutations = (
            ("source", b"third state"),
            ("backup", b"tampered backup"),
            ("manifest", b"{}"),
        )
        for mutation, changed_bytes in mutations:
            with self.subTest(mutation=mutation), TemporaryDirectory() as temporary:
                root = Path(temporary)
                save_directory = root / "game" / "Save"
                save_directory.mkdir(parents=True)
                source = save_directory / "Save001.dat"
                source.write_bytes(b"old")
                transaction_directory = root / "transactions" / "one"
                transaction_directory.mkdir(parents=True)
                backup = transaction_directory / source.name
                backup.write_bytes(b"old")
                original_hash = hashlib.sha256(b"old").hexdigest().upper()
                transaction_path = transaction_directory / "transaction.json"
                transaction_path.write_text(
                    json.dumps(
                        {
                            "schema": 1,
                            "source": str(source.resolve()),
                            "backup": str(backup.resolve()),
                            "original_sha256": original_hash,
                            "candidate_sha256": hashlib.sha256(b"new")
                            .hexdigest()
                            .upper(),
                        }
                    ),
                    encoding="utf-8",
                )
                real_validate = storage_module._validate_claimed_transaction

                def mutate_after_claim(
                    *args: object,
                    source_path: Path = source,
                    backup_path: Path = backup,
                    claimed_directory: Path = transaction_directory,
                    mutation_name: str = mutation,
                    replacement: bytes = changed_bytes,
                    validate: object = real_validate,
                    **kwargs: object,
                ) -> None:
                    target = {
                        "source": source_path,
                        "backup": backup_path,
                        "manifest": claimed_directory / "transaction.resolving.json",
                    }[mutation_name]
                    target.write_bytes(replacement)
                    assert callable(validate)
                    validate(*args, **kwargs)

                with patch(
                    "bfmod.storage._validate_claimed_transaction",
                    side_effect=mutate_after_claim,
                ):
                    recovery = recover_transactions(
                        root / "transactions",
                        save_directory=save_directory,
                    )

                self.assertEqual(recovery[0].state, "needs_attention")
                self.assertTrue(transaction_path.exists())

    def test_recovery_rejects_linked_transaction_manifest_source_or_backup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            save_directory = root / "game" / "Save"
            save_directory.mkdir(parents=True)
            source = save_directory / "Save001.dat"
            source.write_bytes(b"old")
            transaction_directory = root / "transactions" / "one"
            transaction_directory.mkdir(parents=True)
            backup = transaction_directory / source.name
            backup.write_bytes(b"old")
            transaction_path = transaction_directory / "transaction.json"
            transaction_path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "source": str(source.resolve()),
                        "backup": str(backup.resolve()),
                        "original_sha256": hashlib.sha256(b"old").hexdigest().upper(),
                        "candidate_sha256": hashlib.sha256(b"new").hexdigest().upper(),
                    }
                ),
                encoding="utf-8",
            )

            cases = (
                ("bfmod.storage.ensure_plain_path", "事务目录"),
                ("bfmod.storage.ensure_plain_path", "事务清单"),
                ("bfmod.storage.ensure_plain_path_chain", "事务源文件"),
                ("bfmod.storage.ensure_plain_path_chain", "事务备份文件"),
            )
            for patched_name, unsafe_description in cases:
                with self.subTest(description=unsafe_description):
                    original = (
                        storage_module.ensure_plain_path_chain
                        if patched_name.endswith("ensure_plain_path_chain")
                        else storage_module.ensure_plain_path
                    )

                    def reject_selected_path(
                        path: Path,
                        *,
                        description: str,
                        allow_missing: bool = False,
                        rejected_description: str = unsafe_description,
                        validate_path: object = original,
                    ) -> None:
                        if description == rejected_description:
                            raise SnapshotError("模拟链接或重解析点")
                        assert callable(validate_path)
                        validate_path(
                            path,
                            description=description,
                            allow_missing=allow_missing,
                        )

                    with patch(patched_name, side_effect=reject_selected_path):
                        recovery = recover_transactions(
                            root / "transactions",
                            save_directory=save_directory,
                        )

                    self.assertEqual(recovery[0].state, "needs_attention")
                    self.assertTrue(transaction_path.exists())


if __name__ == "__main__":
    unittest.main()
