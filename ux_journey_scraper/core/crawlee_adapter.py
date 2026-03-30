"""
Crawlee-powered crawl engine.

Replaces the custom AutonomousCrawler with crawlee-python's PlaywrightCrawler
for battle-tested crawling (fingerprints, request queue, retries, link discovery).

Our analysis layer (screenshots, page analysis, compliance data, form filling,
journey recording) runs on top of crawlee's page loading.
"""

import asyncio
import logging
import random
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ux_journey_scraper.config.scrape_config import PlatformConfig, ScrapeConfig
from ux_journey_scraper.core.behavior_sequencer import BehaviorSequencer
from ux_journey_scraper.core.compliance_data_collector import ComplianceDataCollector
from ux_journey_scraper.core.cookie_jar import CookieJar
from ux_journey_scraper.core.form_filler import FormFiller
from ux_journey_scraper.core.journey_recorder import Journey, JourneyStep
from ux_journey_scraper.core.navigation_randomizer import NavigationRandomizer
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.page_readiness import PageReadinessEngine
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager
from ux_journey_scraper.core.sitemap_parser import SitemapParser

from ux_journey_scraper.core.design_data_collector import DesignDataCollector
from ux_journey_scraper.core.page_classifier import PageClassifier
from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector
from ux_journey_scraper.core.page_selector import PageSelector

try:
    from ux_journey_scraper.core.cdp_element_detector import CDPElementDetector

    _CDP_AVAILABLE = True
except ImportError:
    _CDP_AVAILABLE = False

try:
    from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

    _CRAWLEE_AVAILABLE = True
except ImportError:
    _CRAWLEE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Block page signatures
BLOCK_SIGNATURES = [
    "access denied",
    "access is temporarily restricted",
    "403 error",
    "403 forbidden",
    "request blocked",
    "captcha",
    "robot",
    "unusual activity",
    "verify you are human",
    "just a moment",
    "security measure",
]


def is_crawlee_available() -> bool:
    """Check if crawlee is installed."""
    return _CRAWLEE_AVAILABLE


class CrawleeAdapter:
    """Bridges crawlee's PlaywrightCrawler to our journey capture pipeline.

    Crawlee handles: request queue, fingerprints, retries, link discovery, anti-bot.
    Our layer handles: screenshots, page analysis, journey JSON, compliance data.
    """

    def __init__(
        self,
        config: ScrapeConfig,
        output_dir: str = "journey_output",
        platform: Optional[PlatformConfig] = None,
        browser_type: str = "webkit",
        cookie_jar: Optional[CookieJar] = None,
        visit_plan=None,  # Optional[VisitPlan] — avoid import if not available
    ):
        if not _CRAWLEE_AVAILABLE:
            raise ImportError(
                "crawlee is not installed. Install with: pip install 'crawlee[playwright]'"
            )

        self.browser_type = browser_type
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)

        self.platform = platform or config.platforms[0]
        self.base_domain = urlparse(config.base_url).netloc.replace("www.", "")

        # Detection avoidance components
        self.cookie_jar = cookie_jar or CookieJar()
        self.visit_plan = visit_plan
        self.behavior = BehaviorSequencer()
        self.readiness = PageReadinessEngine(timeout_ms=15000)

        # Our analysis components
        self.screenshot_manager = ScreenshotManager(
            output_dir=self.output_dir / "screenshots",
            blur_pii=True,
        )
        self.page_analyzer = PageAnalyzer()
        self.compliance_collector = ComplianceDataCollector()
        self.form_filler = FormFiller(config.form_fill)
        self.cdp_detector = CDPElementDetector() if _CDP_AVAILABLE else None
        self.design_collector = DesignDataCollector()

        # Journey state
        self.journey = Journey(
            start_url=config.base_url,
            viewport=(
                self.platform.viewport.get("width", 1920),
                self.platform.viewport.get("height", 1080),
            ),
            platform_type=self.platform.type,
            user_agent=self.platform.user_agent,
        )
        self.pages_captured = 0
        self.captured_urls = set()

    def _is_block_page(self, title: str, text_preview: str) -> bool:
        """Check if the current page is a block/CAPTCHA page."""
        combined = (title + " " + text_preview).lower()
        return any(sig in combined for sig in BLOCK_SIGNATURES)

    def _effective_max_pages(self) -> int:
        """Return max pages — 0 means unlimited (crawl all unique pages)."""
        if self.visit_plan:
            return self.visit_plan.max_pages
        return self.config.crawler.max_pages

    def _calculate_page_delay(self, page_type: str) -> float:
        """Inter-page delay in seconds with page-type modifier and beta jitter.

        Longer delays for transactional pages (cart, checkout) where real users
        spend more time. Shorter delays for informational pages (policy, info).
        Beta distribution gives natural variance skewed toward shorter pauses.
        """
        base_ms = self.config.session_strategy.page_delay_ms
        modifiers = {
            "policy": 0.5, "info": 0.5, "content": 0.7, "homepage": 0.8,
            "search": 0.8, "plp": 1.0, "pdp": 1.0, "cart": 1.3,
            "checkout": 1.3, "account": 1.0, "other": 0.8,
        }
        modifier = modifiers.get(page_type, 0.8)
        adjusted_ms = base_ms * modifier
        t = random.betavariate(2, 5)
        delay_ms = adjusted_ms * 0.5 + adjusted_ms * t
        return delay_ms / 1000.0

    async def crawl(self) -> Journey:
        """Run a full-site crawl using crawlee's PlaywrightCrawler.

        Discovery strategy:
        1. Parse sitemap.xml for all known URLs (instant bulk discovery)
        2. Crawl each page and extract links (catches pages not in sitemap)
        3. Combined = maximum page coverage

        Returns:
            Journey object with all captured steps.
        """
        effective_max = self._effective_max_pages()
        logger.info(f"Starting crawlee crawl: {self.config.target.get('name', 'Unknown')}")
        logger.info(f"Base URL: {self.config.base_url}")
        logger.info(f"Platform: {self.platform.type}")
        logger.info(f"Max pages: {effective_max or 'unlimited'}")

        # Phase 1: Sitemap discovery — find all known URLs upfront
        sitemap_limit = effective_max * 2 if effective_max else 10000
        sitemap = SitemapParser(
            self.config.base_url,
            max_urls=sitemap_limit,
        )
        sitemap_urls = await sitemap.discover_all()

        # Build seed URLs: start URL + sitemap URLs
        seed_urls = [self.config.base_url]
        if sitemap_urls:
            logger.info(f"Sitemap: {len(sitemap_urls)} URLs discovered, seeding crawl queue")
            # Add sitemap URLs (crawlee deduplicates automatically)
            seed_urls.extend(sitemap_urls)
        else:
            logger.info("No sitemap found, relying on link discovery from pages")

        # Phase 2: Smart page selection + navigation randomization
        if sitemap_urls:
            selected = PageSelector.select(seed_urls, self.base_domain)
            logger.info(f"Smart selection: {len(selected)} pages to capture")

            if self.visit_plan:
                seed_urls = NavigationRandomizer.randomize(
                    selected,
                    session_goal=self.visit_plan.goal,
                    target_page_types=self.visit_plan.target_page_types,
                )
                logger.info(f"Navigation randomized for goal: {self.visit_plan.goal}")
            else:
                seed_urls = [s["url"] for s in selected]

        # Phase 3: Crawl with crawlee
        viewport = self.platform.viewport or {"width": 1920, "height": 1080}

        # use_incognito_pages=True required for WebKit (avoids persistent
        # context which calls CDP setDownloadBehavior — unsupported in WebKit)
        # Handler timeout must accommodate behavior simulation (up to 90s dwell)
        # + inter-page delay (up to 45s) + page load + analysis
        handler_timeout = timedelta(minutes=5)

        # Concurrency=1: real users don't open 10 tabs simultaneously
        from crawlee import ConcurrencySettings
        concurrency = ConcurrencySettings(
            min_concurrency=1,
            max_concurrency=1,
        )

        crawler = PlaywrightCrawler(
            max_requests_per_crawl=effective_max or None,  # 0/None = unlimited
            headless=self.config.crawler.headless,
            browser_type=self.browser_type,
            max_request_retries=self.config.crawler.max_retries,
            use_incognito_pages=True,
            request_handler_timeout=handler_timeout,
            concurrency_settings=concurrency,
        )

        # Pre-navigation hook: inject cookies + fix webdriver
        @crawler.pre_navigation_hook
        async def on_before_navigation(context):
            page = context.page
            # Inject cookies from jar
            cookies = self.cookie_jar.get(self.base_domain)
            if cookies:
                try:
                    await page.context.add_cookies(cookies)
                except Exception as e:
                    logger.debug(f"Cookie injection failed: {e}")
            # Attach network listener for compliance data
            self.compliance_collector.attach(page)

            # Fix navigator.webdriver
            try:
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => false,
                        configurable: true
                    })
                """)
            except Exception as e:
                logger.debug(f"Webdriver fix failed: {e}")

        @crawler.router.default_handler
        async def handle_page(context: PlaywrightCrawlingContext) -> None:
            page = context.page
            url = page.url

            # Skip external pages
            if self.base_domain not in urlparse(url).netloc:
                logger.debug(f"Skipping external: {url[:80]}")
                return

            # URL dedup
            normalized = url.split("?")[0].split("#")[0].rstrip("/")
            if normalized in self.captured_urls:
                logger.debug(f"Already captured: {url[:80]}")
                return

            # === READINESS (must run before content checks for JS-rendered SPAs) ===
            await self.readiness.wait_until_ready(page)

            # Get page info for block/empty detection
            title = await page.title() or ""
            try:
                text_preview = await page.evaluate(
                    "document.body?.innerText?.slice(0, 500) || ''"
                )
            except Exception:
                text_preview = ""

            # Block page detection
            if AntiCrawlerDetector.is_block_page(title, text_preview):
                logger.warning(f"Block page detected: {title} at {url[:60]}")
                return

            # Empty page detection
            if AntiCrawlerDetector.is_empty_page(await page.content(), text_preview):
                logger.warning(f"Empty page detected: {url[:60]}")
                return

            self.pages_captured += 1
            self.captured_urls.add(normalized)
            step_num = self.pages_captured

            label = f"{effective_max}" if effective_max else "all"
            logger.info(
                f"[{step_num}/{label}] "
                f"Captured: {title[:50]} | {url[:60]}"
            )

            # Classify page type early (needed for behavior + delay)
            page_type = PageClassifier.classify_url(url)

            # === HUMAN BEHAVIOR ===
            try:
                await self.behavior.run(page, page_type=page_type)
            except Exception as e:
                logger.warning(f"Behavior sequence failed: {e}")

            # === ANALYSIS LAYER ===

            # 1. Screenshot (page scrolled to top by behavior sequencer)
            screenshot_path = None
            try:
                screenshot_path = await self.screenshot_manager.capture_screenshot(
                    page, step_num
                )
            except Exception as e:
                logger.warning(f"Screenshot failed: {e}")

            # 2. Page analysis (forms, links, buttons, CTAs, navigation)
            page_data = {}
            try:
                page_data = await self.page_analyzer.analyze_page(page)
            except Exception as e:
                logger.warning(f"Page analysis failed: {e}")
                page_data = {"url": url, "title": title}

            # Classify page type
            page_data["page_type"] = PageClassifier.classify_url(url)

            # 3. Compliance data (cookies, localStorage, network, tab order)
            try:
                compliance_data = await self.compliance_collector.collect(
                    page, context.page.context
                )
                page_data.update(compliance_data)
            except Exception as e:
                logger.warning(f"Compliance data collection failed: {e}")

            # 3.5 Design system data (for DS builder)
            try:
                design_data = await self.design_collector.collect(page)
                page_data["css_variables"] = design_data.get("css_variables", {})
                page_data["component_tree"] = design_data.get("component_tree", [])
                page_data["asset_urls"] = design_data.get("asset_urls", {})
                if isinstance(page_data.get("computed_styles"), list):
                    page_data["computed_styles"] = {"text_elements": page_data["computed_styles"]}
                elif not isinstance(page_data.get("computed_styles"), dict):
                    page_data["computed_styles"] = {}
                page_data["computed_styles"]["all_elements"] = design_data.get("all_styles", [])
            except Exception as e:
                logger.warning(f"Design data collection failed: {e}")

            # 4. Framework detection
            if self.cdp_detector:
                try:
                    framework = await self.cdp_detector.detect_framework(page)
                    page_data["framework"] = framework
                except Exception:
                    page_data["framework"] = "unknown"

            # 5. Form filling
            try:
                fill_result = await self.form_filler.fill_all_forms(page)
                if fill_result["fields_filled"] > 0:
                    logger.info(f"Filled {fill_result['fields_filled']} form fields")
            except Exception as e:
                logger.warning(f"Form fill failed: {e}")

            # 6. Build journey step
            step = JourneyStep(
                step_number=step_num,
                url=url,
                title=title,
                screenshot_path=screenshot_path,
                page_data=page_data,
            )
            self.journey.add_step(step)

            # 7. Cookie harvest
            try:
                page_cookies = await context.page.context.cookies()
                if page_cookies:
                    self.cookie_jar.update(self.base_domain, page_cookies)
            except Exception as e:
                logger.warning(f"Cookie harvest failed: {e}")

            # 8. Enqueue internal links (crawlee handles dedup)
            try:
                await context.enqueue_links(strategy="same-domain")
            except Exception as e:
                logger.debug(f"Link enqueue failed: {e}")

            # 9. Inter-page delay (human pacing)
            delay = self._calculate_page_delay(page_type)
            logger.debug(f"Inter-page delay: {delay:.1f}s ({page_type})")
            await asyncio.sleep(delay)

        # Run the crawl with all seed URLs (sitemap + base URL)
        await crawler.run(seed_urls)

        # Complete journey
        self.journey.complete()

        # Clean up crawlee storage artifacts
        storage_dir = Path("storage")
        if storage_dir.exists():
            shutil.rmtree(storage_dir, ignore_errors=True)
            logger.debug("Cleaned up crawlee storage directory")

        logger.info(f"Crawl complete: {self.pages_captured} pages captured")

        return self.journey

    def get_stats(self):
        """Get crawler statistics."""
        return {
            "pages_captured": self.pages_captured,
            "engine": "crawlee",
            "captured_urls": len(self.captured_urls),
        }
