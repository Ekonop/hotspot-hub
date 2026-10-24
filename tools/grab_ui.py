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

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

# The offscreen platform has no KColorScheme / plasma theme, so it would grab a
# meaningless light-mode render. Force the Breeze-Dark palette we ship with.
if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
    from PyQt6.QtGui import QColor, QPalette  # noqa: E402

    pal = QPalette()
    win, base, btn, txt, sel = (QColor("#353637"), QColor("#0d1017"),
                                QColor("#31363b"), QColor("#e8e8e8"), QColor("#3daee9"))
    pal.setColor(QPalette.ColorRole.Window, win)
    pal.setColor(QPalette.ColorRole.WindowText, txt)
    pal.setColor(QPalette.ColorRole.Base, base)
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#232526"))
    pal.setColor(QPalette.ColorRole.Text, txt)
    pal.setColor(QPalette.ColorRole.Button, btn)
    pal.setColor(QPalette.ColorRole.ButtonText, txt)
    pal.setColor(QPalette.ColorRole.Highlight, sel)
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#10151d"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#232629"))
    pal.setColor(QPalette.ColorRole.ToolTipText, txt)
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9aa0a6"))
    for g in (QPalette.ColorGroup.Disabled, QPalette.ColorGroup.Inactive):
        pal.setColor(g, QPalette.ColorRole.WindowText, QColor("#7a7f85"))
        pal.setColor(g, QPalette.ColorRole.ButtonText, QColor("#7a7f85"))
    app.setPalette(pal)

# Build the panel and dropdown, give it a generous size so layout settles.
panel = hub.HotspotPanel()
panel.build_ui() if hasattr(panel, "build_ui") else None
dropdown = hub.DropdownPanel(panel)
dropdown.resize(460, 1200)
dropdown.show()
app.processEvents()
time.sleep(0.4)
app.processEvents()

# The show-fade animation may leave opacity < 1 in offscreen rendering;
# force it to full before grabbing.
if hasattr(dropdown, "_effect"):
    dropdown._effect.setOpacity(1.0)
app.processEvents()
time.sleep(0.2)
app.processEvents()

pix = dropdown.grab()
out = os.path.join(os.path.dirname(__file__), "ui_screenshot.png")
pix.save(out)
print("saved", out, pix.width(), "x", pix.height())
