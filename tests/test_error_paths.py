"""Tests for error paths, input validation, and edge cases."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ux_journey_scraper.core.journey_recorder import Journey, JourneyStep
from ux_journey_scraper.core.profile_manager import ProfileManager
from ux_journey_scraper.config.scrape_config import (
    CrawlerConfig,
    PlatformConfig,
    ScrapeConfig,
)


class TestJourneyErrorTracking(unittest.TestCase):

    def test_add_error_records_error(self):
        j = Journey("https://example.com")
        j.add_error("https://example.com/page1", "timeout", phase="screenshot")
        self.assertEqual(len(j.errors), 1)
        self.assertEqual(j.errors[0]["phase"], "screenshot")
        self.assertEqual(j.errors[0]["url"], "https://example.com/page1")

    def test_errors_in_to_dict(self):
        j = Journey("https://example.com")
        j.add_error("https://example.com", "fail", phase="behavior")
        d = j.to_dict()
        self.assertTrue(d["has_errors"])
        self.assertEqual(len(d["errors"]), 1)

    def test_no_errors_key_when_empty(self):
        j = Journey("https://example.com")
        d = j.to_dict()
        self.assertNotIn("errors", d)
        self.assertNotIn("has_errors", d)

    def test_errors_roundtrip_save_load(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            filepath = f.name

        j = Journey("https://example.com")
        j.add_step(JourneyStep(1, "https://example.com", "Home", None, {}))
        j.add_error("https://example.com", "timeout", phase="screenshot")
        j.complete()
        j.save(filepath)

        loaded = Journey.load(filepath)
        self.assertEqual(len(loaded.errors), 1)
        self.assertEqual(loaded.errors[0]["phase"], "screenshot")
        Path(filepath).unlink()

    def test_schema_version_2_2(self):
        j = Journey("https://example.com")
        d = j.to_dict()
        self.assertEqual(d["schema_version"], "2.2")

    def test_error_stringifies_exception(self):
        j = Journey("https://example.com")
        j.add_error("https://example.com", ValueError("bad data"), phase="analysis")
        self.assertEqual(j.errors[0]["error"], "bad data")


class TestJourneyStepLoadHtml(unittest.TestCase):

    def test_load_html_from_inline(self):
        step = JourneyStep(1, "https://example.com", "Home", None, {"html": "<html>test</html>"})
        self.assertEqual(step.load_html(), "<html>test</html>")

    def test_load_html_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write("<html>from file</html>")
            filepath = f.name

        step = JourneyStep(1, "https://example.com", "Home", None, {"html_path": filepath})
        self.assertEqual(step.load_html(), "<html>from file</html>")
        Path(filepath).unlink()

    def test_load_html_missing_file_returns_empty(self):
        step = JourneyStep(1, "https://example.com", "Home", None, {"html_path": "/nonexistent/file.html"})
        self.assertEqual(step.load_html(), "")

    def test_load_html_no_html_returns_empty(self):
        step = JourneyStep(1, "https://example.com", "Home", None, {})
        self.assertEqual(step.load_html(), "")


class TestCrawleeAdapterValidation(unittest.TestCase):

    def _make_config(self, viewport=None):
        return ScrapeConfig(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=[PlatformConfig(type="web_desktop", viewport=viewport or {"width": 1920, "height": 1080})],
            crawler=CrawlerConfig(max_pages=50),
            seed_urls=["https://example.com"],
            auth=None,
        )

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_invalid_browser_type_raises(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        with self.assertRaises(ValueError) as ctx:
            CrawleeAdapter(config=self._make_config(), browser_type="safari")
        self.assertIn("safari", str(ctx.exception))

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_valid_browser_types_accepted(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        for bt in ("webkit", "chromium", "firefox"):
            adapter = CrawleeAdapter(config=self._make_config(), browser_type=bt)
            self.assertEqual(adapter.browser_type, bt)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_zero_viewport_raises(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        with self.assertRaises(ValueError) as ctx:
            CrawleeAdapter(config=self._make_config(viewport={"width": 0, "height": 1080}))
        self.assertIn("viewport", str(ctx.exception).lower())

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_negative_viewport_raises(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        with self.assertRaises(ValueError):
            CrawleeAdapter(config=self._make_config(viewport={"width": -100, "height": 1080}))

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_string_viewport_raises(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        with self.assertRaises((ValueError, TypeError)):
            CrawleeAdapter(config=self._make_config(viewport={"width": "wide", "height": 1080}))

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_get_stats_includes_new_fields(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        adapter = CrawleeAdapter(config=self._make_config())
        stats = adapter.get_stats()
        self.assertIn("consecutive_blocks", stats)
        self.assertIn("allowed_domains", stats)
        self.assertIn("errors", stats)
        self.assertEqual(stats["errors"], 0)


class TestProfileManagerValidation(unittest.TestCase):

    def test_invalid_browser_type_raises(self):
        import asyncio
        pm = ProfileManager(profile_path=Path(tempfile.mkdtemp()) / "profile.json")
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(pm.warm_up(browser_type="opera"))
        self.assertIn("opera", str(ctx.exception))


class TestJourneySaveFailure(unittest.TestCase):

    def test_save_to_invalid_path_raises(self):
        j = Journey("https://example.com")
        j.add_step(JourneyStep(1, "https://example.com", "Home", None, {}))
        j.complete()
        with self.assertRaises(Exception):
            j.save("/proc/nonexistent/dir/journey.json")


if __name__ == "__main__":
    unittest.main()
