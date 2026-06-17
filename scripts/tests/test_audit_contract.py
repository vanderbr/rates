# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_contract.py"


def load_audit_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_contract", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("audit module spec should load")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class AuditContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audit = load_audit_module()

    def test_source_archive_manifest_is_not_a_proto_dataset_manifest(self) -> None:
        manifest_path = REPO_ROOT / "sources" / "irs-revenue-rulings" / "manifest.json"

        self.assertFalse(self.audit.manifest_requires_proto(manifest_path))

    def test_dataset_manifest_requires_proto_metadata(self) -> None:
        manifest_path = REPO_ROOT / "afr" / "manifest.json"

        self.assertTrue(self.audit.manifest_requires_proto(manifest_path))


if __name__ == "__main__":
    unittest.main()
