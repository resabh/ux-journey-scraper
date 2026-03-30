"""Tests for CrawlOrchestrator rewiring to CrawleeAdapter."""

import tempfile
import unittest
from pathlib import Path

from ux_journey_scraper.config.scrape_config import (
    AuthConfig,
    CrawlerConfig,
    PlatformConfig,
    ScrapeConfig,
    SessionStrategy,
)


class TestOrchestratorUsesProfileManager(unittest.TestCase):

    def test_orchestrator_has_profile_manager(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator
        from ux_journey_scraper.core.profile_manager import ProfileManager

        config = self._make_config()
        pm = ProfileManager(profile_path=Path(tempfile.mkdtemp()) / "profile.json")
        orch = CrawlOrchestrator(config, profile_manager=pm)
        self.assertIs(orch.profile_manager, pm)

    def test_orchestrator_default_profile_manager(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator
        from ux_journey_scraper.core.profile_manager import ProfileManager

        config = self._make_config()
        orch = CrawlOrchestrator(config)
        self.assertIsInstance(orch.profile_manager, ProfileManager)

    def _make_config(self):
        return ScrapeConfig(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=[PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080})],
            auth=AuthConfig(logged_out=True, logged_in=False),
            crawler=CrawlerConfig(max_pages=20),
            session_strategy=SessionStrategy(mode="split"),
            seed_urls=["https://example.com"],
        )


if __name__ == "__main__":
    unittest.main()
