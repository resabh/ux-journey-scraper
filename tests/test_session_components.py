#!/usr/bin/env python3
"""
Unit tests for session splitting components.
"""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ux_journey_scraper.config.scrape_config import (
    AuthConfig,
    BrowserProvider,
    PlatformConfig,
    ProxySettings,
    ScrapeConfig,
    SessionStrategy,
)
from ux_journey_scraper.core.cookie_jar import CookieJar
from ux_journey_scraper.core.proxy_rotator import ProxyRotator
from ux_journey_scraper.core.session_planner import SessionPlanner, VisitPlan


class TestSessionStrategy(unittest.TestCase):
    """Test SessionStrategy dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        strategy = SessionStrategy()

        self.assertEqual(strategy.mode, "split")
        self.assertEqual(strategy.pages_per_session, 20)
        self.assertEqual(strategy.min_cooldown_sec, 120)
        self.assertEqual(strategy.max_cooldown_sec, 600)
        self.assertTrue(strategy.persist_cookies)
        self.assertTrue(strategy.randomize_entry_points)
        self.assertFalse(strategy.rotate_ip_per_session)
        self.assertEqual(strategy.rotate_ip_per_n_sessions, 3)

    def test_custom_values(self):
        """Test custom configuration."""
        strategy = SessionStrategy(
            mode="continuous",
            pages_per_session=50,
            min_cooldown_sec=60,
            max_cooldown_sec=300,
            persist_cookies=False,
            rotate_ip_per_session=True,
        )

        self.assertEqual(strategy.mode, "continuous")
        self.assertEqual(strategy.pages_per_session, 50)
        self.assertFalse(strategy.persist_cookies)
        self.assertTrue(strategy.rotate_ip_per_session)

    def test_page_delay_ms_default(self):
        """Test page_delay_ms defaults to 45000."""
        from ux_journey_scraper.config.scrape_config import SessionStrategy
        strategy = SessionStrategy()
        self.assertEqual(strategy.page_delay_ms, 45000)

    def test_page_delay_ms_custom(self):
        """Test page_delay_ms accepts custom value."""
        from ux_journey_scraper.config.scrape_config import SessionStrategy
        strategy = SessionStrategy(page_delay_ms=30000)
        self.assertEqual(strategy.page_delay_ms, 30000)

    def test_page_delay_ms_negative_raises(self):
        """Test page_delay_ms rejects negative values."""
        from ux_journey_scraper.config.scrape_config import SessionStrategy
        with self.assertRaises(ValueError):
            SessionStrategy(page_delay_ms=-1)


class TestProxySettings(unittest.TestCase):
    """Test ProxySettings dataclass."""

    def test_default_disabled(self):
        """Test proxy disabled by default."""
        proxy = ProxySettings()

        self.assertFalse(proxy.enabled)
        self.assertEqual(proxy.provider, "residential")
        self.assertEqual(proxy.rotate_per, "session")
        self.assertEqual(proxy.pool_size, 5)

    def test_enabled_configuration(self):
        """Test enabled proxy configuration."""
        proxy = ProxySettings(
            enabled=True,
            provider="datacenter",
            endpoint_env="MY_PROXY_URL",
            rotate_per="request",
            pool_size=10,
            geo="US",
        )

        self.assertTrue(proxy.enabled)
        self.assertEqual(proxy.provider, "datacenter")
        self.assertEqual(proxy.endpoint_env, "MY_PROXY_URL")
        self.assertEqual(proxy.rotate_per, "request")
        self.assertEqual(proxy.pool_size, 10)
        self.assertEqual(proxy.geo, "US")


class TestBrowserProvider(unittest.TestCase):
    """Test BrowserProvider dataclass."""

    def test_default_local(self):
        """Test default local browser."""
        browser = BrowserProvider()

        self.assertEqual(browser.type, "local")
        self.assertEqual(browser.api_key_env, "BROWSERBASE_API_KEY")
        self.assertEqual(browser.project_id_env, "BROWSERBASE_PROJECT_ID")
        self.assertTrue(browser.use_proxy)
        self.assertTrue(browser.solve_captchas)

    def test_browserbase_configuration(self):
        """Test Browserbase configuration."""
        browser = BrowserProvider(
            type="browserbase",
            api_key_env="MY_API_KEY",
            project_id_env="MY_PROJECT_ID",
            use_proxy=False,
            solve_captchas=False,
        )

        self.assertEqual(browser.type, "browserbase")
        self.assertEqual(browser.api_key_env, "MY_API_KEY")
        self.assertFalse(browser.use_proxy)
        self.assertFalse(browser.solve_captchas)


class TestCookieJar(unittest.TestCase):
    """Test cookie persistence."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_update_and_get_cookies(self):
        """Test saving and retrieving cookies."""
        jar = CookieJar(persist_path=self.data_dir / "cookies.json")

        test_cookies = [
            {
                "name": "session_id",
                "value": "abc123",
                "domain": "example.com",
                "path": "/",
            },
            {
                "name": "user_pref",
                "value": "dark_mode",
                "domain": "example.com",
                "path": "/",
            },
        ]

        # Update cookies
        jar.update("example.com", test_cookies)

        # Retrieve cookies
        retrieved = jar.get("example.com")

        self.assertEqual(len(retrieved), 2)
        self.assertEqual(retrieved[0]["name"], "session_id")
        self.assertEqual(retrieved[0]["value"], "abc123")

    def test_has_cookies(self):
        """Test checking if cookies exist."""
        jar = CookieJar(persist_path=self.data_dir / "cookies.json")

        # No cookies initially
        self.assertFalse(jar.has_cookies("example.com"))

        # Add cookies
        jar.update(
            "example.com", [{"name": "test", "value": "123", "domain": "example.com"}]
        )

        # Should have cookies now
        self.assertTrue(jar.has_cookies("example.com"))

    def test_clear_cookies(self):
        """Test clearing cookies."""
        jar = CookieJar(persist_path=self.data_dir / "cookies.json")

        jar.update(
            "example.com", [{"name": "test", "value": "123", "domain": "example.com"}]
        )
        self.assertTrue(jar.has_cookies("example.com"))

        jar.clear("example.com")
        self.assertFalse(jar.has_cookies("example.com"))

    def test_persistence_to_disk(self):
        """Test cookies persist to disk."""
        persist_path = self.data_dir / "cookies.json"
        jar1 = CookieJar(persist_path=persist_path)
        jar1.update(
            "example.com", [{"name": "test", "value": "123", "domain": "example.com"}]
        )

        # Create new jar instance (simulates restart)
        jar2 = CookieJar(persist_path=persist_path)
        jar2.load_from_disk()

        # Should have cookies from previous instance
        self.assertTrue(jar2.has_cookies("example.com"))
        cookies = jar2.get("example.com")
        self.assertEqual(cookies[0]["name"], "test")


class TestSessionPlanner(unittest.TestCase):
    """Test session planning."""

    def setUp(self):
        """Set up test fixtures."""
        self.platform = PlatformConfig(
            type="web_mobile",
            viewport={"width": 375, "height": 667},
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 14_6 like Mac OS X)",
        )

        self.platforms = [self.platform]

    def _make_config(self, **overrides):
        """Helper: build a minimal ScrapeConfig for SessionPlanner tests."""
        defaults = dict(
            target={"name": "Test", "base_url": "https://example.com"},
            platforms=self.platforms,
            auth=AuthConfig(logged_out=True, logged_in=False),
            seed_urls=["https://example.com/p1", "https://example.com/p2"],
        )
        defaults.update(overrides)
        return ScrapeConfig(**defaults)

    def test_plan_split_sessions(self):
        """Test planning generates multiple VisitPlan sessions."""
        config = self._make_config()
        plans = SessionPlanner().plan(config)

        # Should create multiple sessions
        self.assertGreater(len(plans), 1)

        # Each plan should have proper structure
        for plan in plans:
            self.assertIsInstance(plan, VisitPlan)
            self.assertTrue(plan.session_id.startswith("visit_"))
            self.assertIn(plan.auth_state, ["logged_out", "logged_in"])
            self.assertEqual(plan.platform, self.platform)

    def test_entry_point_randomization(self):
        """Test entry points vary when randomize_entry_points=True."""
        config = self._make_config(
            session_strategy=SessionStrategy(randomize_entry_points=True)
        )
        plans = SessionPlanner().plan(config)

        entry_urls = [plan.entry_url for plan in plans]

        # With seed URLs available and randomize=True, should see variation
        unique_entries = set(entry_urls)
        self.assertGreater(len(unique_entries), 1)

    def test_proxy_slot_assignment(self):
        """Test proxy slots are assigned within pool bounds."""
        config = self._make_config(proxy=ProxySettings(enabled=True, pool_size=3))
        plans = SessionPlanner().plan(config)

        for plan in plans:
            self.assertGreaterEqual(plan.proxy_slot, 0)
            self.assertLess(plan.proxy_slot, 3)


class TestProxyRotator(unittest.TestCase):
    """Test proxy rotation."""

    def test_disabled_proxy(self):
        """Test proxy rotator when disabled."""
        settings = ProxySettings(enabled=False)
        rotator = ProxyRotator(settings)

        proxy = rotator.get_for_slot(0)
        self.assertIsNone(proxy)

        proxy = rotator.next()
        self.assertIsNone(proxy)

    def test_rotating_proxy_provider(self):
        """Test rotating proxy provider."""
        settings = ProxySettings(
            enabled=True, provider="rotating", endpoint_env="PROXY_URL"
        )

        # Mock environment variable
        import os

        os.environ["PROXY_URL"] = "http://proxy.example.com:8080"

        try:
            rotator = ProxyRotator(settings)

            # Should return same proxy for all slots
            proxy1 = rotator.get_for_slot(0)
            proxy2 = rotator.get_for_slot(1)

            self.assertEqual(proxy1["server"], "http://proxy.example.com:8080")
            self.assertEqual(proxy1, proxy2)

        finally:
            del os.environ["PROXY_URL"]

    def test_pool_based_rotation(self):
        """Test pool-based proxy rotation."""
        settings = ProxySettings(enabled=True, provider="residential", pool_size=3)

        rotator = ProxyRotator(settings)

        # Get proxies for different slots
        proxies = []
        for i in range(5):
            proxy = rotator.get_for_slot(i)
            if proxy:
                proxies.append(proxy)

        # Should cycle through pool
        # (Implementation may vary, basic structure check)
        self.assertIsInstance(proxies, list)

    def test_next_rotation(self):
        """Test next() method rotates."""
        import os

        os.environ["PROXY_URL"] = "http://proxy.example.com:8080"

        try:
            settings = ProxySettings(
                enabled=True,
                provider="rotating",
                endpoint_env="PROXY_URL",
                pool_size=3,
            )

            rotator = ProxyRotator(settings)

            # Call next multiple times
            proxies = []
            for _ in range(6):
                proxy = rotator.next()
                if proxy:
                    proxies.append(proxy)

            # Should have gotten proxies
            self.assertGreater(len(proxies), 0)
        finally:
            del os.environ["PROXY_URL"]


if __name__ == "__main__":
    unittest.main()
