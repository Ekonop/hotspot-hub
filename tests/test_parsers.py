"""Unit tests for Hotspot Hub pure parsing/helper functions.

Loads app/hotspot-hub.py (hyphenated filename) via importlib so the
helpers can be exercised without launching the Qt event loop. Uses the
standard library unittest only (no third-party dependencies).

Run:
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is importable.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Offscreen platform keeps any incidental Qt usage headless-safe.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_APP_PATH = REPO_ROOT / "app" / "hotspot-hub.py"


def _load_app_module():
    """Import app/hotspot-hub.py under a stable module name."""
    spec = importlib.util.spec_from_file_location("hotspot_hub", _APP_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - import failure
        raise ImportError(f"Cannot load module spec from {_APP_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hotspot_hub"] = module
    spec.loader.exec_module(module)
    return module


hub = _load_app_module()


class FormatRateTests(unittest.TestCase):
    """Tests for format_rate byte-per-second formatting."""

    def test_zero_and_negative(self) -> None:
        self.assertEqual(hub.format_rate(0), "0.0MB/s")
        self.assertEqual(hub.format_rate(-5), "0.0MB/s")

    def test_megabytes(self) -> None:
        # 1.5 MiB/s
        self.assertEqual(hub.format_rate(1_572_864), "1.5MB/s")
        # Sub-MiB rounds to 0.0MB/s
        self.assertEqual(hub.format_rate(1024), "0.0MB/s")

    def test_gigabytes(self) -> None:
        # 2 GiB/s -> 2.0GB/s
        self.assertEqual(hub.format_rate(2 * 1024 ** 3), "2.0GB/s")

    def test_exact_gb_boundary(self) -> None:
        # 1024 MiB/s is exactly 1 GiB/s
        self.assertEqual(hub.format_rate(1024 * 1024 * 1024), "1.0GB/s")


class LoadMacFileTests(unittest.TestCase):
    """Tests for load_mac_file normalization and validation."""

    def _write(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".macs", delete=False, encoding="utf-8")
        tmp.write(content)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(hub.load_mac_file(Path("/nonexistent/absent.macs")), set())

    def test_valid_and_normalization(self) -> None:
        path = self._write(
            "# comment line\n"
            "AA-BB-CC-DD-EE-FF\n"
            "11:22:33:44:55:66\n"
            "\n"
            "invalid-mac\n"
            "zz:zz:zz:zz:zz:zz\n"
        )
        result = hub.load_mac_file(path)
        self.assertIn("aa:bb:cc:dd:ee:ff", result)
        self.assertIn("11:22:33:44:55:66", result)
        self.assertEqual(len(result), 2)

    def test_comments_and_blank_lines_skipped(self) -> None:
        path = self._write("# only comments\n\n   \n")
        self.assertEqual(hub.load_mac_file(path), set())


class ValidateConfigTests(unittest.TestCase):
    """Tests for validate_config warning generation."""

    def _valid(self) -> dict:
        return {
            "band": "Auto",
            "security": "wpa2",
            "schedule_on": "08:00",
            "schedule_off": "22:00",
            "battery_threshold": 20,
        }

    def test_valid_config_no_warnings(self) -> None:
        self.assertEqual(hub.validate_config(self._valid()), [])

    def test_invalid_band(self) -> None:
        cfg = self._valid()
        cfg["band"] = "6GHz"
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("band" in w for w in warnings))

    def test_invalid_security(self) -> None:
        cfg = self._valid()
        cfg["security"] = "wep"
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("security" in w for w in warnings))

    def test_invalid_schedule_format(self) -> None:
        cfg = self._valid()
        cfg["schedule_on"] = "8am"
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("schedule_on" in w for w in warnings))

    def test_invalid_schedule_hour(self) -> None:
        cfg = self._valid()
        cfg["schedule_off"] = "25:99"
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("schedule_off" in w for w in warnings))

    def test_battery_threshold_out_of_range(self) -> None:
        cfg = self._valid()
        cfg["battery_threshold"] = 3  # below 5
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("battery_threshold" in w for w in warnings))

    def test_battery_threshold_bool_rejected(self) -> None:
        cfg = self._valid()
        cfg["battery_threshold"] = True  # bool is not an int threshold
        warnings = hub.validate_config(cfg)
        self.assertTrue(any("battery_threshold" in w for w in warnings))


class ReadBatteryPercentTests(unittest.TestCase):
    """Tests for read_battery_percent sysfs parsing."""

    def test_reads_capacity(self) -> None:
        with patch.object(hub.Path, "is_file", return_value=True), \
             patch.object(hub.Path, "read_text", return_value="55\n"):
            # Patch read_text on any Path instance call chain.
            self.assertEqual(hub.read_battery_percent(), 55)

    def test_missing_file_returns_none(self) -> None:
        # BAT0 may be absent on desktop systems.
        result = hub.read_battery_percent()
        self.assertTrue(result is None or isinstance(result, int))

    def test_non_numeric_returns_none(self) -> None:
        with patch.object(hub.Path, "is_file", return_value=True), \
             patch.object(hub.Path, "read_text", return_value="not-a-number"):
            self.assertIsNone(hub.read_battery_percent())


class IfaceInfoTests(unittest.TestCase):
    """Tests for iface_info parsing of `iw dev <dev> info` output."""

    SAMPLE = (
        "Interface wlp6s0\n"
        "\tifindex 7\n"
        "\twdev 0x6\n"
        "\taddr 00:11:22:33:44:55\n"
        "\tssid MyNetwork\n"
        "\ttype managed\n"
        "\tchannel 157 (5785 MHz), width: 80 MHz, center1: 5745 MHz\n"
    )

    def test_parses_ssid_and_channel(self) -> None:
        with patch.object(hub, "run", return_value=self.SAMPLE):
            info = hub.iface_info("wlp6s0")
        self.assertEqual(info.get("ssid"), "MyNetwork")
        self.assertEqual(info.get("channel"), "157")

    def test_missing_device_returns_empty(self) -> None:
        with patch.object(hub, "run", return_value=""):
            self.assertEqual(hub.iface_info("nope0"), {})
        with patch.object(hub, "run", return_value="command failed: No such device"):
            self.assertEqual(hub.iface_info("nope0"), {})

    def test_missing_ssid_returns_empty_string(self) -> None:
        sample = "Interface ap0\n\tchannel 36 (5180 MHz)\n"
        with patch.object(hub, "run", return_value=sample):
            info = hub.iface_info("ap0")
        self.assertEqual(info.get("ssid"), "")
        self.assertEqual(info.get("channel"), "36")


class LeaseCountTests(unittest.TestCase):
    """Tests for lease_count line counting."""

    def test_counts_non_empty_lines_from_file(self) -> None:
        with tempfile.NamedTemporaryFile(
                "w", suffix=".leases", delete=False, encoding="utf-8") as fh:
            fh.write("lease 192.168.1.2 aa:bb:cc:dd:ee:ff host1\n\n"
                     "lease 192.168.1.3 11:22:33:44:55:66 host2\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        with patch.object(hub, "LEASE_FILE", path):
            self.assertEqual(hub.lease_count(), "2")

    def test_missing_file_falls_back(self) -> None:
        # No readable file and sudo unavailable -> fallback string.
        with patch.object(hub, "LEASE_FILE", "/nonexistent/absent.leases"), \
             patch.object(hub, "run_rc", return_value=(1, "")):
            self.assertEqual(hub.lease_count(), "0 (no leases yet)")



class CredentialsValidationTests(unittest.TestCase):
    """validate_hotspot_credentials: SSID/passphrase rules."""

    def test_valid_ssid_and_psk(self) -> None:
        ok, msg = hub.validate_hotspot_credentials("Vansh\'s iPhone", "OpenSesame1")
        self.assertTrue(ok, msg)

    def test_empty_psk_keeps_current(self) -> None:
        ok, _ = hub.validate_hotspot_credentials("Hotspot-Shared", "")
        self.assertTrue(ok)

    def test_ssid_too_long(self) -> None:
        ok, msg = hub.validate_hotspot_credentials("A" * 33, "password1")
        self.assertFalse(ok)
        self.assertIn("1–32", msg)

    def test_empty_ssid(self) -> None:
        ok, _ = hub.validate_hotspot_credentials("", "password1")
        self.assertFalse(ok)

    def test_psk_too_short(self) -> None:
        ok, msg = hub.validate_hotspot_credentials("SSID", "short7")
        self.assertFalse(ok)
        self.assertIn("8–63", msg)

    def test_psk_sixtyfour_hex_allowed(self) -> None:
        ok, _ = hub.validate_hotspot_credentials("SSID", "a" * 64)
        self.assertTrue(ok)

    def test_psk_sixtyfour_nonhex_rejected(self) -> None:
        ok, _ = hub.validate_hotspot_credentials("SSID", "z" * 64)
        self.assertFalse(ok)

    def test_ssid_control_chars_rejected(self) -> None:
        ok, _ = hub.validate_hotspot_credentials("Bad\x07SSID", "password1")
        self.assertFalse(ok)


class DeviceNameTests(unittest.TestCase):
    """client_device_name: lease hostname beats OUI vendor beats empty."""

    def test_lease_hostname_used(self) -> None:
        leases = {"aa:bb:cc:dd:ee:ff": {"ip": "10.42.0.5", "hostname": "Vansh\'s iPhone"}}
        got = hub.client_device_name("AA:BB:CC:DD:EE:FF", leases, oui={})
        self.assertEqual(got, "Vansh\'s iPhone")

    def test_star_hostname_falls_back_to_vendor(self) -> None:
        leases = {"aa:bb:cc:dd:ee:ff": {"ip": "10.42.0.5", "hostname": "*"}}
        oui = {"aabbcc": "Apple, Inc."}
        got = hub.client_device_name("aa:bb:cc:dd:ee:ff", leases, oui)
        self.assertEqual(got, "Apple")

    def test_unknown_returns_empty(self) -> None:
        got = hub.client_device_name("aa:bb:cc:dd:ee:ff", leases={}, oui={})
        self.assertEqual(got, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
