"""Tests for ProfileManager — persistent browser profile for detection avoidance."""

import json
import tempfile
import unittest
from pathlib import Path

from ux_journey_scraper.core.profile_manager import ProfileManager
from ux_journey_scraper.core.cookie_jar import CookieJar


class TestProfileManager(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_is_fresh_when_no_profile(self):
        pm = ProfileManager(profile_path=self.profile_path)
        self.assertTrue(pm.is_fresh())

    def test_is_fresh_after_save(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("google.com", [{"name": "NID", "value": "123", "domain": ".google.com", "path": "/"}])
        self.assertFalse(pm.is_fresh())

    def test_merge_cookies_persists(self):
        pm1 = ProfileManager(profile_path=self.profile_path)
        pm1.merge_cookies("google.com", [{"name": "NID", "value": "abc", "domain": ".google.com", "path": "/"}])
        pm2 = ProfileManager(profile_path=self.profile_path)
        cookies = pm2.get_cookies_for_domain("google.com")
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["name"], "NID")

    def test_merge_preserves_other_domains(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("google.com", [{"name": "NID", "value": "1", "domain": ".google.com", "path": "/"}])
        pm.merge_cookies("youtube.com", [{"name": "YSC", "value": "2", "domain": ".youtube.com", "path": "/"}])
        self.assertEqual(len(pm.get_cookies_for_domain("google.com")), 1)
        self.assertEqual(len(pm.get_cookies_for_domain("youtube.com")), 1)

    def test_get_cookies_returns_all(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("google.com", [{"name": "NID", "value": "1", "domain": ".google.com", "path": "/"}])
        pm.merge_cookies("youtube.com", [{"name": "YSC", "value": "2", "domain": ".youtube.com", "path": "/"}])
        all_cookies = pm.get_cookies()
        self.assertEqual(len(all_cookies), 2)

    def test_seed_cookie_jar(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("google.com", [{"name": "NID", "value": "1", "domain": ".google.com", "path": "/"}])
        pm.merge_cookies("tasva.com", [{"name": "sess", "value": "x", "domain": ".tasva.com", "path": "/"}])
        jar = CookieJar()
        pm.seed_cookie_jar(jar, "tasva.com")
        self.assertTrue(jar.has_cookies("google.com"))
        self.assertTrue(jar.has_cookies("tasva.com"))

    def test_absorb_cookie_jar(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("google.com", [{"name": "NID", "value": "1", "domain": ".google.com", "path": "/"}])
        jar = CookieJar()
        jar.update("tasva.com", [{"name": "sess", "value": "abc", "domain": ".tasva.com", "path": "/"}])
        pm.absorb_cookie_jar(jar)
        self.assertEqual(len(pm.get_cookies_for_domain("google.com")), 1)
        self.assertEqual(len(pm.get_cookies_for_domain("tasva.com")), 1)

    def test_is_fresh_stale_after_days(self):
        pm = ProfileManager(profile_path=self.profile_path, max_age_days=7)
        data = {
            "saved_at": "2026-03-01T00:00:00",
            "domains": {"google.com": [{"name": "NID", "value": "1", "domain": ".google.com", "path": "/"}]},
        }
        self.profile_path.write_text(json.dumps(data))
        pm2 = ProfileManager(profile_path=self.profile_path, max_age_days=7)
        self.assertTrue(pm2.is_fresh())

    def test_warmup_sites_constant(self):
        urls = [s["url"] for s in ProfileManager.WARMUP_SITES]
        self.assertTrue(any("google.com" in u for u in urls))
        self.assertTrue(any("youtube.com" in u for u in urls))
        self.assertTrue(any("amazon.com" in u for u in urls))
        self.assertTrue(any("wikipedia.org" in u for u in urls))
        # Should have diverse sites for persona building
        self.assertGreaterEqual(len(ProfileManager.WARMUP_SITES), 8)


if __name__ == "__main__":
    unittest.main()
