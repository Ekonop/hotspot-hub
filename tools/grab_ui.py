#!/usr/bin/env python3
"""Launch the Hotspot Hub dropdown panel, force-render it, and save a PNG screenshot."""
import os
import sys
import time

# Ensure headless-safe offscreen rendering unless a real display is present.
if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import importlib.util

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app", "hotspot-hub.py")
spec = importlib.util.spec_from_file_location("hotspot_hub", APP_PATH)
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)

from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

# Build the panel and dropdown, give it a generous size so layout settles.
panel = hub.HotspotPanel()
panel.build_ui() if hasattr(panel, "build_ui") else None
dropdown = hub.DropdownPanel(panel)
dropdown.resize(460, 1200)
dropdown.show()
app.processEvents()
time.sleep(0.4)
app.processEvents()

pix = dropdown.grab()
out = os.path.join(os.path.dirname(__file__), "ui_screenshot.png")
pix.save(out)
print("saved", out, pix.width(), "x", pix.height())
