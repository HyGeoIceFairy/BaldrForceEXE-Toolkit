"""无窗口、默认只读的后台诊断命令。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .capabilities import build_capability_matrix
from .force_crash_validation import (
    inspect_force_crash_candidate,
    write_force_crash_validation_record,
)
from .huffman import HuffmanFormatError, decode_container
from .installation import InstallationError, resolve_game_root
from .pac import PacArchive
from .realtime_profiles import select_realtime_profile
from .snapshot import SnapshotError, create_verified_snapshot, sha256_file
from .versioning import InstallationIdentity, identify_installation
from .waz import parse_waz
from .workspace import load_workspace

CONFIG_DECODED_SIZE = 0xA8200
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT_ROOT = PROJECT_ROOT / "work" / "baseline"
KEY_FILES = (
    "BaldrForce.exe",
    "BaldrForce.exe.bak",
    "Start-BaldrForce.exe",
    "BFWT.htm",
    "BFWT.xls",
    "Table.pac",
    "Exe.pac",
)


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _file_record(path: Path, root: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": path.relative_to(root).as_posix(),
        "size": stat.st_size,
        "modified_utc": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "sha256": _sha256(path),
    }


def _collect_file_records(
    game_root: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    files: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    paths = [
        *(game_root / relative for relative in KEY_FILES),
        *sorted(
            (game_root / "Save").glob("*"),
            key=lambda item: item.name.casefold(),
        ),
    ]
    for path in paths:
        if not path.is_file():
            continue
        try:
            files.append(_file_record(path, game_root))
        except OSError as error:
            failures.append(
                {
                    "path": path.relative_to(game_root).as_posix(),
                    **_error_section(error),
                }
            )
    return files, failures


def _error_section(error: Exception) -> dict[str, object]:
    return {
        "status": "error",
        "error": f"{type(error).__name__}: {error}",
    }


def _inspect_config(config_path: Path) -> dict[str, object]:
    try:
        config = decode_container(
            config_path.read_bytes(),
            expected_size=CONFIG_DECODED_SIZE,
        )
    except (OSError, ValueError) as error:
        return _error_section(error)
    return {
        "status": "ok",
        "encoded_size": config.encoded_size,
        "decoded_size": len(config.decoded),
        "decoded_sha256": hashlib.sha256(config.decoded).hexdigest().upper(),
        "leaf_count": config.leaf_count,
        "consumed_bits": config.consumed_bits,
    }


def _inspect_pac(game_root: Path, pac_name: str) -> dict[str, object]:
    try:
        archive = PacArchive((game_root / pac_name).read_bytes())
        entry = archive.find("EquipmentInfo.DAT")
        extracted = archive.extract(entry)
    except (OSError, ValueError, KeyError) as error:
        return {
            "archive": pac_name,
            **_error_section(error),
        }
    return {
        "archive": pac_name,
        "status": "ok",
        "version": archive.version,
        "entry": asdict(entry),
        "decoded_sha256": hashlib.sha256(extracted.data).hexdigest().upper(),
        "consumed_size": extracted.consumed_size,
        "trailing_size": extracted.trailing_size,
        "description_preview_cp932": extracted.data[:160]
        .decode("cp932", errors="replace")
        .rstrip(" \0"),
    }


def _inspect_waz(game_root: Path) -> dict[str, object]:
    path = game_root / "Dat" / "Waza" / "TOORU.WAZ"
    try:
        raw = path.read_bytes()
        archive = parse_waz(raw)
    except (OSError, ValueError) as error:
        return _error_section(error)
    return {
        "status": "ok",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest().upper(),
        "declared_record_count": archive.declared_record_count,
        "record_count": len(archive.records),
    }


def _unknown_identity(error: Exception) -> InstallationIdentity:
    return InstallationIdentity(
        name="安装身份检查失败",
        fingerprints={},
        is_known=False,
        read_supported=False,
        write_supported=False,
        reasons=(f"安装身份检查失败：{error}",),
    )


def _inspect(
    game_root: Path,
    reference_table: Path | None = None,
) -> dict[str, object]:
    workspace = None
    workspace_error: Exception | None = None
    try:
        workspace = load_workspace(
            game_root,
            reference_table,
            allow_no_healthy_slots=True,
        )
    except (OSError, ValueError) as error:
        workspace_error = error

    identity_error: Exception | None = None
    if workspace is not None:
        identity = workspace.identity
    else:
        try:
            identity = identify_installation(game_root)
        except (OSError, ValueError) as error:
            identity_error = error
            identity = _unknown_identity(error)

    realtime_profile_error: Exception | None = None
    try:
        selected_realtime_profile = select_realtime_profile(identity, game_root)
    except (OSError, ValueError) as error:
        realtime_profile_error = error
        selected_realtime_profile = None
    capabilities = build_capability_matrix(
        identity,
        (
            selected_realtime_profile.definition
            if selected_realtime_profile is not None
            else None
        ),
    )

    config_report = _inspect_config(game_root / "Save" / "Config.dat")
    pac_reports = [
        _inspect_pac(game_root, pac_name) for pac_name in ("Table.pac", "Exe.pac")
    ]
    waz_report = _inspect_waz(game_root)

    files, file_failures = _collect_file_records(game_root)

    return {
        "schema": 1,
        "created_utc": datetime.now(UTC).isoformat(),
        "game_root": str(game_root),
        "reference_table": str(
            workspace.reference_table
            if workspace is not None
            else reference_table or game_root / "BFWT.htm"
        ),
        "workspace": (
            {"status": "ok"}
            if workspace_error is None
            else _error_section(workspace_error)
        ),
        "installation": {
            "status": "ok" if identity_error is None else "error",
            "name": identity.name,
            "known": identity.is_known,
            "write_supported": identity.write_supported,
            "reasons": identity.reasons,
            "fingerprints": identity.fingerprints,
        },
        "realtime_profile": (
            _error_section(realtime_profile_error)
            if realtime_profile_error is not None
            else (
                {
                    "status": "ok",
                    "id": selected_realtime_profile.definition.id,
                    "name": selected_realtime_profile.definition.display_name,
                    "read_validation": selected_realtime_profile.definition.read_validation,
                    "write_validation": selected_realtime_profile.definition.write_validation,
                }
                if selected_realtime_profile is not None
                else None
            )
        ),
        "capabilities": [asdict(capability) for capability in capabilities],
        "weapon_count": len(workspace.catalog) if workspace is not None else 0,
        "weapon_number_range": (
            [
                workspace.catalog[0].official_number,
                workspace.catalog[-1].official_number,
            ]
            if workspace is not None and workspace.catalog
            else []
        ),
        "force_crash_count": (
            len(workspace.force_crash_catalog) if workspace is not None else 0
        ),
        "slots": [
            {
                "file": slot.path.name,
                "size": len(slot.raw),
                "sha256": slot.sha256,
                "editable": slot.is_editable,
                "completed_weapons": sum(
                    slot.progress.records[index * 3].development_state == 5
                    for index in range(63)
                ),
                "unlocked_force_crashes": sum(
                    slot.progress.records[index].development_state == 5
                    for index in range(192, 213)
                ),
            }
            for slot in (() if workspace is None else workspace.slots)
        ],
        "slot_failures": [
            {
                "file": failure.path.name,
                "number": failure.number,
                "message": failure.message,
            }
            for failure in (() if workspace is None else workspace.slot_failures)
        ],
        "config": config_report,
        "equipment_info": pac_reports,
        "waz": waz_report,
        "files": files,
        "file_failures": file_failures,
    }


def _resolve_existing_file(path_text: str) -> Path:
    path = Path(path_text).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"不是文件：{path}")
    return path


def _assert_output_under_project(output: Path) -> None:
    try:
        output.resolve().relative_to(PROJECT_ROOT)
    except ValueError as error:
        raise ValueError("基线输出必须位于 BaldrForceModifier 开发目录内") from error


def command_inspect(args: argparse.Namespace) -> int:
    game_root = resolve_game_root(args.game_root)
    reference_table = (
        _resolve_existing_file(args.reference_table)
        if args.reference_table is not None
        else None
    )
    report = _inspect(game_root, reference_table)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_snapshot(args: argparse.Namespace) -> int:
    game_root = resolve_game_root(args.game_root)
    reference_table = (
        _resolve_existing_file(args.reference_table)
        if args.reference_table is not None
        else None
    )
    output_root = Path(args.output).resolve()
    _assert_output_under_project(output_root)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = output_root / timestamp
    report = _inspect(game_root, reference_table)
    create_verified_snapshot(
        game_root / "Save",
        target,
        metadata={"diagnostics": report},
    )
    print(target)
    return 0


def command_validate_force_crash(args: argparse.Namespace) -> int:
    """只读核验隔离候选并创建待人工填写的实机闭环记录。"""

    source = _resolve_existing_file(args.source)
    candidate = _resolve_existing_file(args.candidate)
    report = inspect_force_crash_candidate(source, candidate)
    output_path = Path(args.output).resolve()
    _assert_output_under_project(output_path)
    output = write_force_crash_validation_record(
        report,
        output_path,
        slot_name=args.slot_name,
    )
    print(output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="只读检查版本、存档和武器资料")
    inspect_parser.add_argument("--game-root")
    inspect_parser.add_argument("--reference-table")
    inspect_parser.set_defaults(handler=command_inspect)

    snapshot_parser = subparsers.add_parser("snapshot", help="复制完整 Save 并生成哈希清单")
    snapshot_parser.add_argument("--game-root")
    snapshot_parser.add_argument("--reference-table")
    snapshot_parser.add_argument(
        "--output", default=str(DEFAULT_SNAPSHOT_ROOT)
    )
    snapshot_parser.set_defaults(handler=command_snapshot)

    force_crash_parser = subparsers.add_parser(
        "validate-force-crash",
        help="只读核验隔离 Force Crash 候选并生成实机记录模板",
    )
    force_crash_parser.add_argument("--source", required=True)
    force_crash_parser.add_argument("--candidate", required=True)
    force_crash_parser.add_argument("--slot-name", required=True)
    force_crash_parser.add_argument("--output", required=True)
    force_crash_parser.set_defaults(handler=command_validate_force_crash)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.handler(args))
    except (
        OSError,
        ValueError,
        HuffmanFormatError,
        InstallationError,
        SnapshotError,
    ) as error:
        print(f"诊断失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

