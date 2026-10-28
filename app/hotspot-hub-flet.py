#!/usr/bin/env python3
"""Hotspot Hub — Flet UI (integrated tray + window).

This launches the full integrated application with both the system tray icon
(pystray) and the Flet window in a single process.

Run: python3 app/hotspot-hub-flet.py
"""
from __future__ import annotations

import os
import sys

os.environ["FLET_APP_ID"] = "hotspot-hub"
os.environ["FLET_HIDE_WINDOW_ON_START"] = "true"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "app"))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "hotspot_hub_tray", os.path.join(REPO, "app", "hotspot-hub-tray.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_mod.run_app()
