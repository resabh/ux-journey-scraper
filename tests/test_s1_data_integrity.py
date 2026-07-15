"""Stage 1 data-integrity regression tests.

Each test maps to a specific S1 item from the remediation plan.
"""

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ux_journey_scraper.config.scrape_config import FormFillConfig, LocationConfig


# --- S1.5: context.json page_type ---

class TestS15PageType(unittest.TestCase):

    def test_page_type_from_page_data(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator

        step = MagicMock()
        step.url = "https://example.com/product"
        step.title = "Product"
        step.screenshot_path = "screenshots/step-001.png"
        step.timestamp = "2026-07-08T00:00:00"
        step.page_data = {"page_type": "pdp"}

        config = MagicMock()
        config.target = {"name": "Test"}
        config.base_url = "https://example.com"
        config.platforms = []
        config.session_strategy.mode = "split"

        with patch.object(CrawlOrchestrator, '__init__', lambda self, *a, **kw: None):
            orch = CrawlOrchestrator.__new__(CrawlOrchestrator)
            orch.config = config
            journey = MagicMock()
            journey.steps = [step]
            screens = orch._extract_screens_from_journey(journey)
            self.assertEqual(screens[0]["page_type"], "pdp")

    def test_page_type_missing_defaults_unknown(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator

        step = MagicMock()
        step.url = "https://example.com"
        step.title = "Home"
        step.screenshot_path = None
        step.timestamp = "2026-07-08T00:00:00"
        step.page_data = {}

        with patch.object(CrawlOrchestrator, '__init__', lambda self, *a, **kw: None):
            orch = CrawlOrchestrator.__new__(CrawlOrchestrator)
            orch.config = MagicMock()
            journey = MagicMock()
            journey.steps = [step]
            screens = orch._extract_screens_from_journey(journey)
            self.assertEqual(screens[0]["page_type"], "unknown")


# --- S1.8: CLI native defaults + appium guard ---

class TestS18CliDefaults(unittest.TestCase):

    def test_default_platforms_web_only(self):
        from ux_journey_scraper.cli.main import cli
        import click.testing
        # Check the --platforms param default
        scrape_cmd = None
        for cmd_name, cmd in cli.commands.items():
            for param in cmd.params:
                if param.name == "platforms":
                    self.assertNotIn("native", param.default)
                    self.assertEqual(param.default, "web_desktop,web_mobile")
                    return
        self.fail("--platforms param not found")

    def test_appium_guard_uses_find_spec(self):
        import ux_journey_scraper.cli.main as cli_mod
        import importlib
        # The guard should use importlib.util.find_spec, not try/except import
        import inspect
        source = inspect.getsource(cli_mod)
        self.assertIn("find_spec", source)
        self.assertNotIn("AppProvisioner", source.split("find_spec")[0][-200:])


# --- S1.9: engine fail-loud ---

class TestS19EngineFail(unittest.TestCase):

    @patch("ux_journey_scraper.core.crawl_orchestrator._CRAWLEE_AVAILABLE", False)
    def test_auto_raises_without_crawlee(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator
        config = MagicMock()
        with self.assertRaises(RuntimeError) as ctx:
            CrawlOrchestrator(config, engine="auto")
        self.assertIn("crawlee", str(ctx.exception).lower())

    @patch("ux_journey_scraper.core.crawl_orchestrator._CRAWLEE_AVAILABLE", False)
    def test_crawlee_raises_without_crawlee(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator
        config = MagicMock()
        with self.assertRaises(RuntimeError):
            CrawlOrchestrator(config, engine="crawlee")

    @patch("ux_journey_scraper.core.crawl_orchestrator._CRAWLEE_AVAILABLE", False)
    def test_local_succeeds_without_crawlee(self):
        from ux_journey_scraper.core.crawl_orchestrator import CrawlOrchestrator
        config = MagicMock()
        orch = CrawlOrchestrator(config, engine="local")
        self.assertEqual(orch.engine, "local")


# --- S1.1: network request misattribution ---

class TestS11NetworkBuckets(unittest.TestCase):

    def test_per_page_isolation(self):
        from ux_journey_scraper.core.compliance_data_collector import ComplianceDataCollector
        collector = ComplianceDataCollector()

        page_a = MagicMock()
        page_a._ux_net_bucket = None
        handlers_a = {}
        def on_a(event, handler):
            handlers_a[event] = handler
        page_a.on = on_a
        page_a._ux_net_bucket = None

        page_b = MagicMock()
        page_b._ux_net_bucket = None
        handlers_b = {}
        def on_b(event, handler):
            handlers_b[event] = handler
        page_b.on = on_b
        page_b._ux_net_bucket = None

        collector.attach(page_a)
        collector.attach(page_b)

        req_a = MagicMock(url="https://a.com/1", method="GET", resource_type="document")
        req_b = MagicMock(url="https://b.com/1", method="GET", resource_type="xhr")
        handlers_a["request"](req_a)
        handlers_b["request"](req_b)

        self.assertEqual(len(page_a._ux_net_bucket), 1)
        self.assertEqual(page_a._ux_net_bucket[0]["url"], "https://a.com/1")
        self.assertEqual(len(page_b._ux_net_bucket), 1)
        self.assertEqual(page_b._ux_net_bucket[0]["url"], "https://b.com/1")

    def test_double_attach_idempotent(self):
        from ux_journey_scraper.core.compliance_data_collector import ComplianceDataCollector
        collector = ComplianceDataCollector()

        call_count = 0
        page = MagicMock()
        page._ux_net_bucket = None
        def on(event, handler):
            nonlocal call_count
            call_count += 1
        page.on = on

        collector.attach(page)
        collector.attach(page)
        self.assertEqual(call_count, 1)

    def test_collect_drains_in_place(self):
        from ux_journey_scraper.core.compliance_data_collector import ComplianceDataCollector
        import asyncio
        collector = ComplianceDataCollector()

        page = MagicMock()
        page._ux_net_bucket = None
        handler_ref = {}
        def on(event, handler):
            handler_ref[event] = handler
        page.on = on

        collector.attach(page)

        req = MagicMock(url="https://x.com", method="GET", resource_type="document")
        handler_ref["request"](req)

        async def _cookies():
            return []
        async def _evaluate(*a, **kw):
            return []

        context = MagicMock()
        context.cookies = _cookies
        page.evaluate = _evaluate

        async def _run():
            r1 = await collector.collect(page, context)
            r2 = await collector.collect(page, context)
            return r1, r2

        result, result2 = asyncio.run(_run())
        self.assertEqual(len(result["network_requests"]), 1)
        self.assertEqual(len(result2["network_requests"]), 0)
        self.assertIsNotNone(getattr(page, "_ux_net_bucket", None))


# --- S1.3: loader html_path resolution ---

class TestS13LoaderHtml(unittest.TestCase):

    def test_externalized_html_loads(self):
        from ux_journey_scraper.journey_loader import JourneyStep
        with tempfile.TemporaryDirectory() as td:
            html_dir = Path(td) / "html"
            html_dir.mkdir()
            html_file = html_dir / "step-1.html"
            html_file.write_text("<html>test</html>", encoding="utf-8")

            step = JourneyStep(
                step_number=1, url="https://example.com", title="Test",
                screenshot_path=Path(td) / "screenshots/step-001.png",
                timestamp="", page_data={"html_path": "html/step-1.html"},
                journey_dir=Path(td),
            )
            self.assertEqual(step.load_html(), "<html>test</html>")

    def test_inline_html_preferred(self):
        from ux_journey_scraper.journey_loader import JourneyStep
        step = JourneyStep(
            step_number=1, url="https://example.com", title="Test",
            screenshot_path=Path("/tmp/x.png"),
            timestamp="", page_data={"html": "<html>inline</html>"},
        )
        self.assertEqual(step.load_html(), "<html>inline</html>")

    def test_missing_file_raises_file_not_found(self):
        from ux_journey_scraper.journey_loader import JourneyStep
        step = JourneyStep(
            step_number=1, url="https://example.com", title="Test",
            screenshot_path=Path("/tmp/x.png"),
            timestamp="", page_data={"html_path": "html/nonexistent.html"},
            journey_dir=Path("/tmp"),
        )
        with self.assertRaises(FileNotFoundError):
            step.load_html()

    def test_neither_key_raises_key_error(self):
        from ux_journey_scraper.journey_loader import JourneyStep
        step = JourneyStep(
            step_number=1, url="https://example.com", title="Test",
            screenshot_path=Path("/tmp/x.png"),
            timestamp="", page_data={},
        )
        with self.assertRaises(KeyError):
            step.load_html()


# --- S1.4: unconditional form filling ---

class TestS14FormFill(unittest.TestCase):

    def test_default_disabled(self):
        cfg = FormFillConfig()
        self.assertFalse(cfg.enabled)

    def test_payment_autocomplete_in_never_fill(self):
        from ux_journey_scraper.core.form_filler import FormFiller
        for key in ("cc-number", "cc-name", "cc-csc", "cc-exp"):
            self.assertIn(key, FormFiller.NEVER_FILL)

    def test_payment_regex_patterns_blocked(self):
        from ux_journey_scraper.core.form_filler import FormFiller
        cfg = FormFillConfig(enabled=True)
        filler = FormFiller(cfg)
        # card_number pattern via name attribute
        val = filler._detect_fill_value("", "card_number", "", "", "text")
        self.assertIsNone(val)

    def test_phone_regex_blocks_hotel(self):
        from ux_journey_scraper.core.form_filler import FormFiller
        cfg = FormFillConfig(enabled=True)
        filler = FormFiller(cfg)
        val = filler._detect_fill_value("", "hotel_name", "", "", "text")
        self.assertIsNone(val)

    def test_phone_regex_matches_tel(self):
        from ux_journey_scraper.core.form_filler import FormFiller
        cfg = FormFillConfig(enabled=True)
        filler = FormFiller(cfg)
        val = filler._detect_fill_value("", "tel", "", "", "text")
        self.assertEqual(val, cfg.phone)

    def test_phone_regex_matches_tel_no(self):
        from ux_journey_scraper.core.form_filler import FormFiller
        cfg = FormFillConfig(enabled=True)
        filler = FormFiller(cfg)
        val = filler._detect_fill_value("", "tel_no", "", "", "text")
        self.assertEqual(val, cfg.phone)


# --- S1.2: fabricated screenshot path ---

class TestS12ScreenshotPath(unittest.TestCase):

    def test_no_fabricated_fallback_in_code(self):
        import inspect
        from ux_journey_scraper.core import crawlee_adapter
        source = inspect.getsource(crawlee_adapter)
        # The old fabrication pattern should be gone
        self.assertNotIn('or f"screenshots/step-', source)


# --- S1.10: screenshot validation ---

class TestS110ScreenshotValidation(unittest.TestCase):

    def test_missing_file_invalid(self):
        from ux_journey_scraper.core.screenshot_manager import validate_screenshot
        valid, err = validate_screenshot("/nonexistent/screenshot.png")
        self.assertFalse(valid)
        self.assertIn("does not exist", err)

    def test_tiny_file_invalid(self):
        from ux_journey_scraper.core.screenshot_manager import validate_screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"tiny")
            path = f.name
        try:
            valid, err = validate_screenshot(path)
            self.assertFalse(valid)
            self.assertIn("too small", err)
        finally:
            Path(path).unlink()

    def test_valid_png_passes(self):
        from ux_journey_scraper.core.screenshot_manager import validate_screenshot
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        img = Image.new("RGB", (400, 400))
        pixels = img.load()
        for y in range(400):
            for x in range(400):
                pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
        img.save(path)
        try:
            valid, err = validate_screenshot(path)
            self.assertTrue(valid, f"Expected valid but got: {err}")
        finally:
            Path(path).unlink()

    def test_single_color_invalid(self):
        from ux_journey_scraper.core.screenshot_manager import validate_screenshot
        from PIL import Image
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        img = Image.new("RGB", (400, 400), color=(255, 255, 255))
        img.save(path)
        try:
            valid, err = validate_screenshot(path)
            self.assertFalse(valid)
            self.assertIn("single-color", err)
        finally:
            Path(path).unlink()


# --- S1.14: duplicate frame detection ---

class TestS114DuplicateFrames(unittest.TestCase):

    def test_duplicate_frames_reported(self):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter
        from PIL import Image

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "web_desktop"
            platform_dir.mkdir()
            ss_dir = platform_dir / "screenshots"
            ss_dir.mkdir()

            # Two identical screenshots
            img = Image.new("RGB", (100, 100), color=(128, 64, 32))
            img.save(ss_dir / "step-001.png")
            img.save(ss_dir / "step-002.png")
            # One different
            img2 = Image.new("RGB", (100, 100), color=(200, 100, 50))
            img2.save(ss_dir / "step-003.png")

            journey = {
                "schema_version": "2.3",
                "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "start_time": "2026-07-08T00:00:00",
                "end_time": "2026-07-08T00:01:00",
                "total_steps": 3,
                "steps": [
                    {"step_number": 1, "url": "https://example.com", "title": "A",
                     "screenshot_path": "screenshots/step-001.png",
                     "timestamp": "2026-07-08T00:00:00",
                     "page_data": {"page_type": "homepage"}},
                    {"step_number": 2, "url": "https://example.com/2", "title": "B",
                     "screenshot_path": "screenshots/step-002.png",
                     "timestamp": "2026-07-08T00:00:10",
                     "page_data": {"page_type": "plp"}},
                    {"step_number": 3, "url": "https://example.com/3", "title": "C",
                     "screenshot_path": "screenshots/step-003.png",
                     "timestamp": "2026-07-08T00:00:20",
                     "page_data": {"page_type": "pdp"}},
                ],
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))

            reporter = CoverageReporter()
            results = reporter.emit_readiness(td)
            readiness = results.get("web_desktop", {})
            self.assertIn("duplicate_frames", readiness)
            self.assertEqual(readiness["duplicate_frames"], [[1, 2]])


# --- S1.6: location readiness ---

class TestS16LocationReadiness(unittest.TestCase):

    @patch("ux_journey_scraper.core.screenshot_manager.validate_screenshot", return_value=(True, ""))
    def test_location_fail_closed_when_unverified(self, _mock_validate):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "web_desktop"
            platform_dir.mkdir()

            journey = {
                "schema_version": "2.3",
                "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "start_time": "2026-07-08T00:00:00",
                "end_time": "2026-07-08T00:01:00",
                "total_steps": 1,
                "steps": [
                    {"step_number": 1, "url": "https://example.com", "title": "A",
                     "screenshot_path": "screenshots/step-001.png",
                     "timestamp": "2026-07-08T00:00:00",
                     "page_data": {"page_type": "homepage",
                                   "navigation": {"primary_nav": [{"text": "Home"}]},
                                   "forms": [{"action": "/search"}],
                                   "search": {"has_search_bar": True}}},
                ],
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))

            config = MagicMock()
            config.location = LocationConfig(pincode="400097")
            reporter = CoverageReporter(config)
            results = reporter.emit_readiness(td, config=config)
            readiness = results.get("web_desktop", {})
            self.assertFalse(readiness["location_established"])
            self.assertFalse(readiness["benchmark_ready"])

    @patch("ux_journey_scraper.core.schema_validator.validate_journey_dict", return_value=[])
    @patch("ux_journey_scraper.core.screenshot_manager.validate_screenshot", return_value=(True, ""))
    def test_location_verified_passes(self, _mock_validate, _mock_schema):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "web_desktop"
            platform_dir.mkdir()

            journey = {
                "schema_version": "2.3",
                "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "platform": {"type": "web_desktop"},
                "start_time": "2026-07-08T00:00:00",
                "end_time": "2026-07-08T00:01:00",
                "total_steps": 5,
                "location_verified": True,
                "steps": [
                    {"step_number": i, "url": f"https://example.com/{pt}", "title": pt,
                     "screenshot_path": f"screenshots/step-{i:03d}.png",
                     "timestamp": "2026-07-08T00:00:00",
                     "page_data": {"page_type": pt, "url": f"https://example.com/{pt}",
                                   "title": pt,
                                   "navigation": {"primary_nav": [{"text": "Home"}]},
                                   "forms": [{"action": "/search"}],
                                   "search": {"has_search_bar": True}}}
                    for i, pt in enumerate(
                        ["homepage", "plp", "pdp", "search_results", "cart"], 1
                    )
                ],
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))

            config = MagicMock()
            config.location = LocationConfig(pincode="400097")
            reporter = CoverageReporter(config)
            results = reporter.emit_readiness(td, config=config)
            readiness = results.get("web_desktop", {})
            self.assertTrue(readiness["location_established"])
            self.assertTrue(readiness["benchmark_ready"])


# --- S1.11: design data shared function ---

class TestS111DesignData(unittest.TestCase):

    def test_single_definition(self):
        import inspect
        from ux_journey_scraper.core import design_data_collector
        source = inspect.getsource(design_data_collector)
        count = source.count("def collect_and_merge_design_data")
        self.assertEqual(count, 1)

    def test_two_call_sites(self):
        import inspect
        from ux_journey_scraper.core import crawlee_adapter, flow_runner
        ca_src = inspect.getsource(crawlee_adapter)
        fr_src = inspect.getsource(flow_runner)
        self.assertIn("collect_and_merge_design_data", ca_src)
        self.assertIn("collect_and_merge_design_data", fr_src)


# --- S1.12: dismisser exclusion guard ---

class TestS112DismisserExclusion(unittest.TestCase):

    def test_no_location_selectors_in_dismiss_lists(self):
        import inspect
        from ux_journey_scraper.core import crawlee_adapter, flow_runner
        for mod in (crawlee_adapter, flow_runner):
            source = inspect.getsource(mod)
            # The close_selectors list should not contain location/pincode/address
            # selectors — they are excluded via the panel_selectors guard
            self.assertNotIn('[class*="address" i]', source.split("close_selectors")[0])
            self.assertNotIn('[class*="pincode" i]', source.split("close_selectors")[0])

    def test_exclusion_guard_present(self):
        import inspect
        from ux_journey_scraper.core import crawlee_adapter, flow_runner
        for mod in (crawlee_adapter, flow_runner):
            source = inspect.getsource(mod)
            self.assertIn("panel_selectors", source)
            self.assertIn("location panel", source.lower())


# --- S1.13: in-stock PDP preference ---

class TestS113PdpAvailability(unittest.TestCase):

    def test_oos_pattern_matches(self):
        from ux_journey_scraper.core.flow_runner import FlowRunner
        pattern = FlowRunner._OOS_PATTERN
        self.assertTrue(pattern.search("Sold Out"))
        self.assertTrue(pattern.search("OUT OF STOCK"))
        self.assertTrue(pattern.search("Notify Me"))
        self.assertTrue(pattern.search("unavailable"))
        self.assertTrue(pattern.search("Coming Soon"))
        self.assertFalse(pattern.search("Add to Cart"))
        self.assertFalse(pattern.search("Buy Now"))


# --- P1a: block-aware capture + readiness (S1.10 item 6) ---

class TestBlockAwareReadiness(unittest.TestCase):

    def test_soft_error_signatures_detected(self):
        from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector
        # tira SPA soft-error page (HTTP 200 with error UI)
        self.assertTrue(
            AntiCrawlerDetector.is_block_page(
                "Tira: Shop", "Something went wrong Go to Home"
            )
        )

    def test_block_signals_returns_matches(self):
        from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector
        signals = AntiCrawlerDetector.block_signals(
            "", "Something went wrong. Please go to home."
        )
        self.assertIn("something went wrong", signals)
        self.assertIn("go to home", signals)

    def test_block_signals_empty_on_live_page(self):
        from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector
        signals = AntiCrawlerDetector.block_signals(
            "Buy Maggi Noodles Online", "Add to Cart. Price Rs 100. In stock."
        )
        self.assertEqual(signals, [])

    @patch("ux_journey_scraper.core.schema_validator.validate_journey_dict", return_value=[])
    @patch("ux_journey_scraper.core.screenshot_manager.validate_screenshot", return_value=(True, ""))
    def test_blocked_step_excluded_from_readiness(self, _mv, _ms):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "flows_web_desktop"
            platform_dir.mkdir()

            steps = []
            for i, pt in enumerate(
                ["homepage", "plp", "pdp", "search_results", "cart"], 1
            ):
                steps.append({
                    "step_number": i, "url": f"https://example.com/{pt}", "title": pt,
                    "screenshot_path": f"screenshots/step-{i:03d}.png",
                    "timestamp": "2026-07-08T00:00:00",
                    "page_data": {"page_type": pt, "url": f"https://example.com/{pt}",
                                  "title": pt, "page_state": "live",
                                  "navigation": {"primary_nav": [{"text": "Home"}]},
                                  "forms": [{"action": "/search"}],
                                  "search": {"has_search_bar": True}},
                })
            # Add a blocked step masquerading as search_results
            steps.append({
                "step_number": 6, "url": "https://example.com/blocked", "title": "Error",
                "screenshot_path": "screenshots/step-006.png",
                "timestamp": "2026-07-08T00:00:00",
                "page_data": {"page_type": "search_results", "url": "https://example.com/blocked",
                              "title": "Error", "page_state": "blocked",
                              "response_metadata": {"status": 200, "blocked": True,
                                                    "block_signals": ["something went wrong"]}},
            })
            journey = {
                "schema_version": "2.3", "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "platform": {"type": "web_desktop"},
                "start_time": "2026-07-08T00:00:00", "end_time": "2026-07-08T00:01:00",
                "total_steps": len(steps), "steps": steps,
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))

            reporter = CoverageReporter()
            results = reporter.emit_readiness(td)
            readiness = results.get("flows_web_desktop", {})
            self.assertFalse(readiness["no_blocked_steps"])
            self.assertIn(6, readiness.get("blocked_steps", []))
            self.assertFalse(readiness["benchmark_ready"])

    def test_combined_readiness_has_environment(self):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "web_desktop"
            platform_dir.mkdir()
            journey = {
                "schema_version": "2.3", "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "environment": "prod",
                "start_time": "2026-07-08T00:00:00", "end_time": "2026-07-08T00:01:00",
                "total_steps": 1,
                "steps": [{"step_number": 1, "url": "https://example.com", "title": "A",
                           "screenshot_path": None, "timestamp": "2026-07-08T00:00:00",
                           "page_data": {"page_type": "homepage", "page_state": "live"}}],
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))
            reporter = CoverageReporter()
            reporter.emit_readiness(td)
            combined = json.loads((td / "readiness_web_desktop.json").read_text())
            self.assertEqual(combined["environment"], "prod")
            self.assertIn("no_blocked_steps", combined)


# --- P1b mechanism 4: occlusion gate ---

class TestOcclusionGate(unittest.TestCase):

    def test_threshold_and_record_helper_exist(self):
        from ux_journey_scraper.core import session_preconditions as sp
        self.assertTrue(hasattr(sp, "measure_occlusion"))
        self.assertTrue(hasattr(sp, "record_occlusion"))
        self.assertGreater(sp.OCCLUSION_THRESHOLD, 0)
        self.assertLess(sp.OCCLUSION_THRESHOLD, 1)

    @patch("ux_journey_scraper.core.schema_validator.validate_journey_dict", return_value=[])
    @patch("ux_journey_scraper.core.screenshot_manager.validate_screenshot", return_value=(True, ""))
    def test_occluded_step_fails_readiness(self, _mv, _ms):
        from ux_journey_scraper.core.coverage_reporter import CoverageReporter

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            platform_dir = td / "flows_web_desktop"
            platform_dir.mkdir()

            steps = []
            for i, pt in enumerate(
                ["homepage", "plp", "pdp", "search_results", "cart"], 1
            ):
                occ = i == 3  # occlude the pdp step
                steps.append({
                    "step_number": i, "url": f"https://example.com/{pt}", "title": pt,
                    "screenshot_path": f"screenshots/step-{i:03d}.png",
                    "timestamp": "2026-07-08T00:00:00",
                    "page_data": {"page_type": pt, "url": f"https://example.com/{pt}",
                                  "title": pt, "page_state": "live",
                                  "overlay_coverage": 0.6 if occ else 0.02,
                                  "occluded": occ,
                                  "navigation": {"primary_nav": [{"text": "Home"}]},
                                  "forms": [{"action": "/search"}],
                                  "search": {"has_search_bar": True}},
                })
            journey = {
                "schema_version": "2.3", "start_url": "https://example.com",
                "viewport": {"width": 1920, "height": 1080},
                "platform": {"type": "web_desktop"},
                "start_time": "2026-07-08T00:00:00", "end_time": "2026-07-08T00:01:00",
                "total_steps": len(steps), "steps": steps,
            }
            (platform_dir / "journey.json").write_text(json.dumps(journey))

            reporter = CoverageReporter()
            results = reporter.emit_readiness(td)
            readiness = results.get("flows_web_desktop", {})
            self.assertFalse(readiness["steps_unoccluded"])
            self.assertIn(3, readiness.get("occluded_steps", []))
            self.assertFalse(readiness["benchmark_ready"])


# --- Journey location_verified serialization ---

class TestJourneyLocationVerified(unittest.TestCase):

    def test_location_verified_in_to_dict(self):
        from ux_journey_scraper.core.journey_recorder import Journey
        j = Journey("https://example.com")
        j.location_verified = True
        d = j.to_dict()
        self.assertTrue(d["location_verified"])

    def test_location_verified_absent_when_none(self):
        from ux_journey_scraper.core.journey_recorder import Journey
        j = Journey("https://example.com")
        d = j.to_dict()
        self.assertNotIn("location_verified", d)


if __name__ == "__main__":
    unittest.main()
