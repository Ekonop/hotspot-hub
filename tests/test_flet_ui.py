"""Tests for Hotspot Hub Flet UI logic and helpers."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load app/hotspot-hub-tray.py dynamically
_spec = importlib.util.spec_from_file_location(
    "hotspot_hub_tray", os.path.join(REPO_ROOT, "app", "hotspot-hub-tray.py")
)
tray_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tray_mod)


class FletUiHelperTests(unittest.TestCase):
    """Test Flet UI utility functions and tray generation."""

    def test_create_tray_icon(self) -> None:
        for state in ("on", "off", "error", "unknown"):
            img = tray_mod.create_tray_icon(state)
            self.assertEqual(img.size, (64, 64))
            self.assertEqual(img.mode, "RGBA")

    def test_doc_path_exists(self) -> None:
        doc = tray_mod.DOC_PATH
        self.assertTrue(os.path.exists(doc), f"DOC_PATH {doc} does not exist")

    def test_design_tokens(self) -> None:
        self.assertEqual(tray_mod.PRIMARY, "#3B82F6")
        self.assertEqual(tray_mod.SUCCESS, "#22C55E")
        self.assertEqual(tray_mod.DANGER, "#EF4444")
        self.assertEqual(tray_mod.FAULT_WINDOW_S, 120)


if __name__ == "__main__":
    unittest.main()
