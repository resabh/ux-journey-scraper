"""Tests for CrawleeAdapter enhancements."""

import unittest
from unittest.mock import patch

from ux_journey_scraper.config.scrape_config import (
    CrawlerConfig,
    PlatformConfig,
    ScrapeConfig,
    SessionStrategy,
)
from ux_journey_scraper.core.cookie_jar import CookieJar


class TestCrawleeAdapterInit(unittest.TestCase):

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_accepts_cookie_jar(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        jar = CookieJar()
        jar.update(
            "example.com",
            [{"name": "t", "value": "1", "domain": ".example.com", "path": "/"}],
        )
        adapter = CrawleeAdapter(config=self._make_config(), cookie_jar=jar)
        self.assertIs(adapter.cookie_jar, jar)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_default_cookie_jar(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        adapter = CrawleeAdapter(config=self._make_config())
        self.assertIsInstance(adapter.cookie_jar, CookieJar)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_accepts_visit_plan(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter
        from ux_journey_scraper.core.session_planner import VisitPlan

        plan = VisitPlan(
            session_id="test",
            goal="browse",
            entry_url="https://example.com",
            entry_strategy="homepage",
            target_page_types=["homepage", "plp", "pdp"],
            max_pages=20,
            auth_state="logged_out",
            platform=PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080}),
            proxy_slot=0,
        )
        adapter = CrawleeAdapter(config=self._make_config(), visit_plan=plan)
        self.assertEqual(adapter.visit_plan, plan)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_visit_plan_overrides_max_pages(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter
        from ux_journey_scraper.core.session_planner import VisitPlan

        plan = VisitPlan(
            session_id="test",
            goal="browse",
            entry_url="https://example.com",
            entry_strategy="homepage",
            target_page_types=["homepage"],
            max_pages=15,
            auth_state="logged_out",
            platform=PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080}),
            proxy_slot=0,
        )
        adapter = CrawleeAdapter(config=self._make_config(), visit_plan=plan)
        self.assertEqual(adapter._effective_max_pages(), 15)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_no_visit_plan_uses_config(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        config = self._make_config()
        adapter = CrawleeAdapter(config=config)
        self.assertEqual(adapter._effective_max_pages(), config.crawler.max_pages)

    def _make_config(self):
        return ScrapeConfig(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=[PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080})],
            crawler=CrawlerConfig(max_pages=50),
            seed_urls=["https://example.com"],
            auth=None,
        )


class TestPageDelayCalculation(unittest.TestCase):

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_policy_pages_get_shorter_delay(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        config = ScrapeConfig(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=[PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080})],
            crawler=CrawlerConfig(max_pages=50),
            session_strategy=SessionStrategy(page_delay_ms=45000),
            seed_urls=["https://example.com"],
            auth=None,
        )
        adapter = CrawleeAdapter(config=config)
        delay = adapter._calculate_page_delay("policy")
        self.assertLessEqual(delay, 30)
        self.assertGreater(delay, 0)

    @patch("ux_journey_scraper.core.crawlee_adapter._CRAWLEE_AVAILABLE", True)
    def test_checkout_pages_get_longer_delay(self):
        from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter

        config = ScrapeConfig(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=[PlatformConfig(type="web_desktop", viewport={"width": 1920, "height": 1080})],
            crawler=CrawlerConfig(max_pages=50),
            session_strategy=SessionStrategy(page_delay_ms=45000),
            seed_urls=["https://example.com"],
            auth=None,
        )
        adapter = CrawleeAdapter(config=config)
        policy_delays = [adapter._calculate_page_delay("policy") for _ in range(50)]
        checkout_delays = [adapter._calculate_page_delay("checkout") for _ in range(50)]
        avg_policy = sum(policy_delays) / len(policy_delays)
        avg_checkout = sum(checkout_delays) / len(checkout_delays)
        self.assertGreater(avg_checkout, avg_policy)


if __name__ == "__main__":
    unittest.main()
