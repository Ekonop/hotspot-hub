"""Tests verifying memory leak prevention fixes."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from core import services as svc


class CacheBoundingTests(unittest.TestCase):
    """Test that in-memory caches prune expired entries and enforce max size."""

    def test_store_cache_prunes_expired_and_bounds_size(self) -> None:
        cache: dict = {}
        now = 100.0
        ttl = 5.0
        # Add 128 entries
        for i in range(128):
            svc._store_cache(cache, f"key_{i}", f"val_{i}", now - 10.0, ttl)

        self.assertEqual(len(cache), 128)
        # Adding a new entry at now should trigger eviction of the expired entries
        svc._store_cache(cache, "new_key", "new_val", now, ttl)
        self.assertIn("new_key", cache)
        self.assertLessEqual(len(cache), 128)

    def test_mdns_cache_bounding(self) -> None:
        # Populate mdns cache with > 64 entries
        svc._mdns_cache.clear()
        for i in range(70):
            svc._mdns_cache[f"10.42.0.{i}"] = (10.0, f"device_{i}")
        # Calling resolve_mdns_name with empty string returns ""
        self.assertEqual(svc.resolve_mdns_name(""), "")
        # Now resolve for a dummy IP, triggering the eviction of expired entries
        svc.resolve_mdns_name("10.42.0.254")
        self.assertLessEqual(len(svc._mdns_cache), 65)


class QtLayoutCleanupTests(unittest.TestCase):
    """Test Qt widget and layout cleanup fixes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_clear_layout_recursively_deletes_sublayouts(self) -> None:
        import hotspot_hub as hub

        container = QWidget()
        main_layout = QVBoxLayout(container)

        sub_layout = QVBoxLayout()
        lbl1 = QLabel("label 1")
        lbl2 = QLabel("label 2")
        sub_layout.addWidget(lbl1)
        sub_layout.addWidget(lbl2)
        main_layout.addLayout(sub_layout)

        self.assertEqual(len(container.findChildren(QLabel)), 2)
        hub._clear_layout(main_layout)
        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(len(container.findChildren(QLabel)), 0)

    def test_dialogs_have_wa_delete_on_close(self) -> None:
        from unittest.mock import MagicMock

        import hotspot_hub as hub

        mock_ctrl = MagicMock()
        mock_ctrl.config = {"mac_filter_enabled": False, "mac_filter_mode": "allow"}
        mock_ctrl.allowed_macs = set()
        mock_ctrl.blocked_macs = set()

        cfg_dlg = hub.ConfigureDialog(mock_ctrl)
        self.assertTrue(cfg_dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
        cfg_dlg.deleteLater()

        mac_dlg = hub.MacFilterDialog(mock_ctrl)
        self.assertTrue(mac_dlg.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
        mac_dlg.deleteLater()

        from PyQt6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
