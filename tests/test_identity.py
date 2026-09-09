"""The product's identity, as the release and the update check see it."""

from __future__ import annotations

from pathlib import Path
import unittest

import _stubs  # noqa: F401

from dominant_control import edition


ROOT = Path(__file__).resolve().parents[1]


class IdentityTests(unittest.TestCase):
    def test_the_application_names_itself(self):
        self.assertEqual("Dominant Control", edition.APP_DISPLAY_NAME)
        self.assertEqual("DominantControl.exe", edition.APP_EXECUTABLE_NAME)
        self.assertEqual("DominantControl", edition.APP_DATA_FOLDER)

    def test_the_update_check_points_at_this_repository(self):
        self.assertTrue(edition.UPDATE_CHECK_AVAILABLE)
        self.assertEqual("nishizumi-maho", edition.UPDATE_REPO_OWNER)
        self.assertEqual("Nishizumi-Control", edition.UPDATE_REPO_NAME)

    def test_every_release_surface_agrees_on_the_version(self):
        version = edition.APP_VERSION
        self.assertEqual("13", version)

        installer = (ROOT / "installer" / "DominantControl_v13.iss").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'#define AppVersion "{version}"', installer)
        self.assertIn(f"OutputBaseFilename=DominantControl_v{version}_Setup", installer)
        self.assertIn(f"VersionInfoVersion={version}.0.0.0", installer)

        version_info = (ROOT / "build" / "version_info.txt").read_text(encoding="utf-8")
        self.assertIn(f"filevers=({version}, 0, 0, 0)", version_info)
        self.assertIn(f"u'ProductVersion', u'{version}.0.0'", version_info)

        portable = (ROOT / "tools" / "build_portable.ps1").read_text(encoding="utf-8")
        self.assertIn(f"DominantControl_Portable_v{version}_Windows_x64.zip", portable)

    def test_the_installer_keeps_the_name_as_its_identity(self):
        installer = (ROOT / "installer" / "DominantControl_v13.iss").read_text(
            encoding="utf-8"
        )
        # Without an explicit AppId, Inno uses AppName: the installer updates
        # the existing installation instead of adding a second product.
        self.assertNotIn("\nAppId=", installer)
        self.assertIn('#define AppName "Dominant Control"', installer)
        self.assertIn("PrivilegesRequired=lowest", installer)

    def test_the_automation_this_build_does_not_ship_is_absent(self):
        """The advantage-giving automation is not merely turned off."""

        self.assertFalse(edition.LAPDIST_MACROS_AVAILABLE)
        self.assertFalse(edition.PIT_LIMITER_AUTOMATION_AVAILABLE)
        self.assertFalse(edition.FUEL_MIXTURE_AUTOMATION_AVAILABLE)
        self.assertFalse(edition.HYBRID_HOLD_AUTOMATION_AVAILABLE)
        self.assertFalse(edition.P2P_CHAIN_AUTOMATION_AVAILABLE)
        self.assertFalse(edition.SECOND_THROTTLE_PIT_MACRO_AVAILABLE)
        # Weather-driven automation is part of the product.
        self.assertTrue(edition.WIPER_AUTOMATION_AVAILABLE)
        self.assertTrue(edition.SURFACE_PROFILE_AUTOMATION_AVAILABLE)


if __name__ == "__main__":
    unittest.main()
