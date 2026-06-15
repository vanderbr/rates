#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Run repository-level audit checks for the published data contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_ROOT = REPO_ROOT / "proto"
PROTO_PACKAGE_ROOT = PROTO_ROOT / "rates" / "v1"
FORBIDDEN_PATHS = (
    PROTO_ROOT / "v1",
    PROTO_ROOT / "vanderbr",
)
FORBIDDEN_FILE_NAMES = {".DS_Store"}
FORBIDDEN_DIR_NAMES = {"__pycache__"}
FORBIDDEN_TEXT_MARKERS = (
    "proto/" "vanderbr",
    "vanderbr/" "rates/v1",
    "vanderbr." "rates.v1",
)


class AuditFailure(Exception):
    """Raised when a repository audit invariant is violated."""

    def __init__(self, failures: list[str]) -> None:
        super().__init__("audit failed")
        self.failures = failures


def iter_repo_paths() -> list[Path]:
    return [path for path in REPO_ROOT.rglob("*") if ".git" not in path.parts]


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def check_local_metadata(paths: list[Path], failures: list[str]) -> None:
    for path in paths:
        if path.name in FORBIDDEN_FILE_NAMES:
            failures.append(f"local metadata file present: {path.relative_to(REPO_ROOT)}")
        if path.is_dir() and path.name in FORBIDDEN_DIR_NAMES:
            failures.append(f"generated local directory present: {path.relative_to(REPO_ROOT)}")


def check_proto_layout(failures: list[str]) -> None:
    if not PROTO_PACKAGE_ROOT.is_dir():
        failures.append("missing proto/rates/v1")
    for forbidden_path in FORBIDDEN_PATHS:
        if forbidden_path.exists():
            failures.append(f"forbidden proto path exists: {forbidden_path.relative_to(REPO_ROOT)}")

    for proto_file in PROTO_PACKAGE_ROOT.glob("*.proto"):
        text = proto_file.read_text(encoding="utf-8")
        if "package rates.v1;" not in text:
            failures.append(f"proto package mismatch: {proto_file.relative_to(REPO_ROOT)}")


def check_forbidden_text(paths: list[Path], failures: list[str]) -> None:
    for path in paths:
        if not path.is_file() or path.suffix == ".pb":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in FORBIDDEN_TEXT_MARKERS:
            if marker in text:
                failures.append(f"forbidden proto namespace marker {marker!r}: {path.relative_to(REPO_ROOT)}")


def check_canonical_json(paths: list[Path], failures: list[str]) -> None:
    for path in paths:
        if path.suffix != ".json" or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            failures.append(f"invalid JSON: {path.relative_to(REPO_ROOT)}")
            continue
        canonical = json.dumps(parsed, indent=2, ensure_ascii=False) + "\n"
        if raw != canonical:
            failures.append(f"non-canonical JSON: {path.relative_to(REPO_ROOT)}")


def manifest_entries(manifest: dict[str, object]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for key in ("file", "data_file", "records"):
        value = manifest.get(key)
        if isinstance(value, dict):
            entries.append(value)
    for key in ("files", "shards", "years", "records"):
        value = manifest.get(key)
        if isinstance(value, list):
            entries.extend(item for item in value if isinstance(item, dict))
    return entries


def check_manifest_references(paths: list[Path], failures: list[str]) -> None:
    all_protobuf_files = {
        path.resolve()
        for path in paths
        if path.is_file() and path.parent.name == "protobuf" and path.suffix == ".pb"
    }
    referenced_protobuf_files: set[Path] = set()

    for manifest_path in paths:
        if manifest_path.name != "manifest.json" or not manifest_path.is_file():
            continue
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            failures.append(f"manifest is not an object: {manifest_path.relative_to(REPO_ROOT)}")
            continue

        proto = manifest.get("proto")
        if not isinstance(proto, dict):
            failures.append(f"manifest missing proto object: {manifest_path.relative_to(REPO_ROOT)}")
        else:
            proto_file = proto.get("file")
            proto_message = proto.get("message")
            if not isinstance(proto_file, str) or not (REPO_ROOT / proto_file).is_file():
                failures.append(f"manifest proto file missing: {manifest_path.relative_to(REPO_ROOT)}")
            if not isinstance(proto_message, str) or not proto_message.startswith("rates.v1."):
                failures.append(f"manifest proto message mismatch: {manifest_path.relative_to(REPO_ROOT)}")

        for entry in manifest_entries(manifest):
            protobuf_path = entry.get("protobuf_path")
            if not isinstance(protobuf_path, str):
                continue
            resolved = (manifest_path.parent / protobuf_path).resolve()
            referenced_protobuf_files.add(resolved)
            if not resolved.is_file():
                failures.append(f"manifest protobuf missing: {manifest_path.relative_to(REPO_ROOT)} -> {protobuf_path}")

    for protobuf_file in sorted(all_protobuf_files - referenced_protobuf_files):
        failures.append(f"unreferenced protobuf shard: {protobuf_file.relative_to(REPO_ROOT)}")


def run() -> None:
    failures: list[str] = []
    paths = iter_repo_paths()
    check_local_metadata(paths, failures)
    check_proto_layout(failures)
    check_forbidden_text(paths, failures)
    check_canonical_json(paths, failures)
    check_manifest_references(paths, failures)
    if failures:
        raise AuditFailure(failures)


def main() -> int:
    try:
        run()
    except AuditFailure as error:
        for failure in error.failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
