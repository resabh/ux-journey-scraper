"""Tests for ProfileManager — persistent browser profile for detection avoidance."""

import json
import tempfile
import time
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
        # Only target domain cookies are seeded (no cross-site leakage)
        self.assertFalse(jar.has_cookies("google.com"))
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


class TestCookieExpiryPruning(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_expired_cookies_pruned_on_load(self):
        past = time.time() - 86400  # 1 day ago
        future = time.time() + 86400  # 1 day from now
        data = {
            "saved_at": "2026-04-27T00:00:00",
            "domains": {
                "example.com": [
                    {"name": "old", "value": "1", "domain": ".example.com", "path": "/", "expires": past},
                    {"name": "valid", "value": "2", "domain": ".example.com", "path": "/", "expires": future},
                ]
            },
        }
        self.profile_path.write_text(json.dumps(data))
        pm = ProfileManager(profile_path=self.profile_path)
        cookies = pm.get_cookies_for_domain("example.com")
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["name"], "valid")

    def test_session_cookies_kept(self):
        """Session cookies (expires=-1) are never pruned."""
        data = {
            "saved_at": "2026-04-27T00:00:00",
            "domains": {
                "example.com": [
                    {"name": "sess", "value": "1", "domain": ".example.com", "path": "/", "expires": -1},
                ]
            },
        }
        self.profile_path.write_text(json.dumps(data))
        pm = ProfileManager(profile_path=self.profile_path)
        self.assertEqual(len(pm.get_cookies_for_domain("example.com")), 1)

    def test_all_expired_removes_domain(self):
        past = time.time() - 86400
        data = {
            "saved_at": "2026-04-27T00:00:00",
            "domains": {
                "dead.com": [
                    {"name": "a", "value": "1", "domain": ".dead.com", "path": "/", "expires": past},
                ]
            },
        }
        self.profile_path.write_text(json.dumps(data))
        pm = ProfileManager(profile_path=self.profile_path)
        self.assertEqual(pm.get_cookies_for_domain("dead.com"), [])


class TestPerCookieMerge(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_merge_upserts_by_name_path(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("example.com", [
            {"name": "A", "value": "1", "domain": ".example.com", "path": "/"},
            {"name": "B", "value": "2", "domain": ".example.com", "path": "/"},
        ])
        # Update A, keep B
        pm.merge_cookies("example.com", [
            {"name": "A", "value": "updated", "domain": ".example.com", "path": "/"},
        ])
        cookies = pm.get_cookies_for_domain("example.com")
        self.assertEqual(len(cookies), 2)
        by_name = {c["name"]: c for c in cookies}
        self.assertEqual(by_name["A"]["value"], "updated")
        self.assertEqual(by_name["B"]["value"], "2")

    def test_merge_different_paths_kept_separate(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("example.com", [
            {"name": "X", "value": "root", "domain": ".example.com", "path": "/"},
            {"name": "X", "value": "api", "domain": ".example.com", "path": "/api"},
        ])
        cookies = pm.get_cookies_for_domain("example.com")
        self.assertEqual(len(cookies), 2)


class TestLocalStoragePersistence(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_merge_and_get_local_storage(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_local_storage("example.com", {"_ga": "GA1.2.123", "theme": "dark"})
        ls = pm.get_local_storage("example.com")
        self.assertEqual(ls["_ga"], "GA1.2.123")
        self.assertEqual(ls["theme"], "dark")

    def test_local_storage_persists_across_loads(self):
        pm1 = ProfileManager(profile_path=self.profile_path)
        pm1.merge_local_storage("example.com", {"key": "value"})
        pm2 = ProfileManager(profile_path=self.profile_path)
        self.assertEqual(pm2.get_local_storage("example.com"), {"key": "value"})

    def test_local_storage_merge_upserts(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_local_storage("example.com", {"a": "1", "b": "2"})
        pm.merge_local_storage("example.com", {"b": "updated", "c": "3"})
        ls = pm.get_local_storage("example.com")
        self.assertEqual(ls, {"a": "1", "b": "updated", "c": "3"})

    def test_seed_cookie_jar_includes_local_storage(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("tasva.com", [{"name": "s", "value": "1", "domain": ".tasva.com", "path": "/"}])
        pm.merge_local_storage("tasva.com", {"cart_id": "abc123"})
        jar = CookieJar()
        pm.seed_cookie_jar(jar, "tasva.com")
        self.assertEqual(jar.get_local_storage("tasva.com"), {"cart_id": "abc123"})

    def test_absorb_cookie_jar_includes_local_storage(self):
        pm = ProfileManager(profile_path=self.profile_path)
        jar = CookieJar()
        jar.update("example.com", [{"name": "t", "value": "1", "domain": ".example.com", "path": "/"}])
        jar.set_local_storage("example.com", {"visited": "true"})
        pm.absorb_cookie_jar(jar)
        self.assertEqual(pm.get_local_storage("example.com"), {"visited": "true"})

    def test_empty_local_storage_returns_empty_dict(self):
        pm = ProfileManager(profile_path=self.profile_path)
        self.assertEqual(pm.get_local_storage("nonexistent.com"), {})


class TestCookieImportExport(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_import_json_list(self):
        pm = ProfileManager(profile_path=self.profile_path)
        cookie_file = Path(self.tmp_dir) / "cookies.json"
        cookie_file.write_text(json.dumps([
            {"name": "NID", "value": "abc", "domain": ".google.com", "path": "/"},
            {"name": "YSC", "value": "xyz", "domain": ".youtube.com", "path": "/"},
        ]))
        count = pm.import_cookies_file(str(cookie_file))
        self.assertEqual(count, 2)
        self.assertEqual(len(pm.get_cookies_for_domain("google.com")), 1)
        self.assertEqual(len(pm.get_cookies_for_domain("youtube.com")), 1)

    def test_import_netscape_format(self):
        pm = ProfileManager(profile_path=self.profile_path)
        cookie_file = Path(self.tmp_dir) / "cookies.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".google.com\tTRUE\t/\tTRUE\t1735689600\tNID\tabc123\n"
            ".youtube.com\tTRUE\t/\tFALSE\t0\tYSC\txyz789\n"
        )
        count = pm.import_cookies_file(str(cookie_file), format="netscape")
        self.assertEqual(count, 2)
        google_cookies = pm.get_cookies_for_domain("google.com")
        self.assertEqual(google_cookies[0]["secure"], True)

    def test_import_browser_extension_json(self):
        """Browser extensions use expirationDate instead of expires."""
        pm = ProfileManager(profile_path=self.profile_path)
        cookie_file = Path(self.tmp_dir) / "cookies.json"
        cookie_file.write_text(json.dumps([
            {"name": "sid", "value": "v1", "domain": ".example.com", "path": "/",
             "expirationDate": 1735689600, "httpOnly": True, "secure": True},
        ]))
        count = pm.import_cookies_file(str(cookie_file))
        self.assertEqual(count, 1)
        c = pm.get_cookies_for_domain("example.com")[0]
        self.assertEqual(c["expires"], 1735689600)
        self.assertTrue(c["httpOnly"])

    def test_import_merges_with_existing(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("example.com", [{"name": "A", "value": "1", "domain": ".example.com", "path": "/"}])
        cookie_file = Path(self.tmp_dir) / "cookies.json"
        cookie_file.write_text(json.dumps([
            {"name": "B", "value": "2", "domain": ".example.com", "path": "/"},
        ]))
        pm.import_cookies_file(str(cookie_file))
        cookies = pm.get_cookies_for_domain("example.com")
        self.assertEqual(len(cookies), 2)

    def test_export_and_reimport(self):
        pm1 = ProfileManager(profile_path=self.profile_path)
        pm1.merge_cookies("example.com", [{"name": "X", "value": "1", "domain": ".example.com", "path": "/"}])
        pm1.merge_local_storage("example.com", {"key": "val"})

        export_path = Path(self.tmp_dir) / "export.json"
        pm1.export_profile(str(export_path))

        pm2 = ProfileManager(profile_path=Path(self.tmp_dir) / "profile2.json")
        count = pm2.import_cookies_file(str(export_path), format="json")
        self.assertGreater(count, 0)

    def test_auto_detect_json(self):
        pm = ProfileManager(profile_path=self.profile_path)
        cookie_file = Path(self.tmp_dir) / "cookies.json"
        cookie_file.write_text(json.dumps([
            {"name": "t", "value": "1", "domain": ".test.com", "path": "/"},
        ]))
        count = pm.import_cookies_file(str(cookie_file), format="auto")
        self.assertEqual(count, 1)

    def test_auto_detect_netscape(self):
        pm = ProfileManager(profile_path=self.profile_path)
        cookie_file = Path(self.tmp_dir) / "cookies.txt"
        cookie_file.write_text(".test.com\tTRUE\t/\tFALSE\t0\tname\tvalue\n")
        count = pm.import_cookies_file(str(cookie_file), format="auto")
        self.assertEqual(count, 1)


class TestProfileHealth(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.profile_path = Path(self.tmp_dir) / "profile.json"

    def test_health_empty_profile(self):
        pm = ProfileManager(profile_path=self.profile_path)
        h = pm.get_health()
        self.assertEqual(h["total_cookies"], 0)
        self.assertEqual(h["domains"], 0)
        self.assertTrue(h["is_fresh"])

    def test_health_with_cookies(self):
        pm = ProfileManager(profile_path=self.profile_path)
        pm.merge_cookies("a.com", [{"name": "x", "value": "1", "domain": ".a.com", "path": "/"}])
        pm.merge_cookies("b.com", [{"name": "y", "value": "2", "domain": ".b.com", "path": "/"}])
        pm.merge_local_storage("a.com", {"k": "v"})
        h = pm.get_health()
        self.assertEqual(h["total_cookies"], 2)
        self.assertEqual(h["domains"], 2)
        self.assertEqual(h["local_storage_domains"], 1)
        self.assertFalse(h["is_fresh"])
        self.assertIsNotNone(h["age_hours"])

    def test_health_counts_expired(self):
        past = time.time() - 86400
        future = time.time() + 86400
        data = {
            "saved_at": "2026-04-27T00:00:00",
            "domains": {
                "example.com": [
                    {"name": "old", "value": "1", "domain": ".example.com", "path": "/", "expires": past},
                    {"name": "new", "value": "2", "domain": ".example.com", "path": "/", "expires": future},
                ]
            },
        }
        self.profile_path.write_text(json.dumps(data))
        # Note: expired cookies are pruned on load, so health shows 0 expired after load
        pm = ProfileManager(profile_path=self.profile_path)
        h = pm.get_health()
        self.assertEqual(h["valid_cookies"], 1)
        self.assertEqual(h["expired_cookies"], 0)  # Pruned on load


if __name__ == "__main__":
    unittest.main()
