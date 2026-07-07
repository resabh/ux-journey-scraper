"""
Crawlee-powered crawl engine.

Replaces the custom AutonomousCrawler with crawlee-python's PlaywrightCrawler
for battle-tested crawling (fingerprints, request queue, retries, link discovery).

Our analysis layer (screenshots, page analysis, compliance data, form filling,
journey recording) runs on top of crawlee's page loading.
"""

import asyncio
import json
import logging
import random
import signal
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
from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector, BLOCK_SIGNATURES
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

VALID_BROWSER_TYPES = ("webkit", "chromium", "firefox")

# Plain-file URLs that are not journeys (e.g. boAt's /agents.md was captured
# as a journey step in corpus v1). Never capture or follow these.
SKIP_URL_EXTENSIONS = (
    ".md", ".txt", ".xml", ".json", ".csv", ".pdf", ".zip", ".gz",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".css", ".js", ".mjs", ".map", ".mp4", ".webm", ".mp3",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


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

        if browser_type not in VALID_BROWSER_TYPES:
            raise ValueError(f"Invalid browser_type: {browser_type!r}. Must be one of {VALID_BROWSER_TYPES}")
        self.browser_type = browser_type
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)

        self.platform = platform or config.platforms[0]
        self.base_domain = urlparse(config.base_url).netloc.replace("www.", "")
        # Track allowed domains — sites like bata.in → bata.com/in/ redirect
        self.allowed_domains = {self.base_domain}

        # Detection avoidance components
        self.cookie_jar = cookie_jar or CookieJar()
        self.visit_plan = visit_plan
        self.behavior = BehaviorSequencer()
        self.readiness = PageReadinessEngine(timeout_ms=15000)

        # Our analysis components
        self.screenshot_manager = ScreenshotManager(
            output_dir=self.output_dir / "screenshots",
            blur_pii=config.crawler.screenshot_blur_pii,
        )
        self.page_analyzer = PageAnalyzer()
        self.compliance_collector = ComplianceDataCollector()
        self.form_filler = FormFiller(config.form_fill)
        self.cdp_detector = CDPElementDetector() if _CDP_AVAILABLE else None
        self.design_collector = DesignDataCollector()

        # Journey state
        vp_width = self.platform.viewport.get("width", 1920)
        vp_height = self.platform.viewport.get("height", 1080)
        if not isinstance(vp_width, int) or not isinstance(vp_height, int) or vp_width <= 0 or vp_height <= 0:
            raise ValueError(f"Invalid viewport dimensions: {vp_width}x{vp_height} — must be positive integers")
        self.journey = Journey(
            start_url=config.base_url,
            viewport=(vp_width, vp_height),
            platform_type=self.platform.type,
            user_agent=self.platform.user_agent,
        )
        self.pages_captured = 0
        self.captured_urls = set()
        self.consecutive_blocks = 0

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
        # Vary beta params slightly per call to avoid statistical fingerprint
        alpha = random.uniform(1.8, 2.5)
        beta = random.uniform(4.0, 5.5)
        t = random.betavariate(alpha, beta)
        delay_ms = adjusted_ms * t
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
        # + inter-page delay (up to 45s) + page load + analysis. On always-active,
        # very-tall gold-label sites (jiomart) one page measures ~200s+ (readiness
        # ~27s + dwell ~109s + analyze ~60s) and the old 5min budget timed out with
        # zero pages captured. Raised to 10min per ADR p005 (gate-approved).
        handler_timeout = timedelta(minutes=10)

        # Concurrency=1: real users don't open 10 tabs simultaneously
        from crawlee import ConcurrencySettings
        concurrency = ConcurrencySettings(
            min_concurrency=1,
            max_concurrency=1,
            desired_concurrency=1,
        )

        # Each crawl needs its own event manager + storage client to avoid
        # stale global state that causes requests to never be processed
        # on consecutive crawls (current_concurrency stays 0).
        import uuid
        from crawlee.configuration import Configuration
        from crawlee.events import EventManager
        from crawlee.storage_clients import MemoryStorageClient

        storage_dir = f"./storage_{uuid.uuid4().hex[:8]}"
        crawl_config = Configuration(storage_dir=storage_dir)
        event_manager = EventManager()
        storage_client = MemoryStorageClient()

        # Apply the platform viewport/UA/locale to the actual browser context.
        # Without this, crawlee's default fingerprint picks its own (desktop)
        # screen and "mobile" crawls render desktop layouts while journey.json
        # claims a mobile viewport (corpus v1 defect #5). browserforge merges
        # an explicit viewport OVER the fingerprint's, so this wins.
        context_options = {"viewport": dict(viewport)}
        if self.platform.user_agent:
            context_options["user_agent"] = self.platform.user_agent
        if getattr(self.platform, "locale", None):
            context_options["locale"] = self.platform.locale
        if getattr(self.platform, "timezone_id", None):
            context_options["timezone_id"] = self.platform.timezone_id

        # Constrain the fingerprint to the platform's device class and screen
        # so headers/screen stay consistent with the forced viewport.
        fingerprint_generator = "default"
        try:
            from crawlee.fingerprint_suite import (
                DefaultFingerprintGenerator,
                HeaderGeneratorOptions,
                ScreenOptions,
            )

            is_mobile = self.platform.type in ("web_mobile", "web_tablet")
            fingerprint_generator = DefaultFingerprintGenerator(
                header_options=HeaderGeneratorOptions(
                    devices=["mobile" if is_mobile else "desktop"],
                ),
                # Loose bounds — browserforge needs a real fingerprint from its
                # dataset; exact-match constraints can be unsatisfiable.
                screen_options=ScreenOptions(
                    min_width=max(viewport["width"] - 100, 0),
                    max_width=viewport["width"] + 200,
                ),
            )
        except Exception as e:
            logger.warning(f"Fingerprint constraints unavailable, using default: {e}")

        crawler = PlaywrightCrawler(
            max_requests_per_crawl=effective_max or None,  # 0/None = unlimited
            headless=self.config.crawler.headless,
            browser_type=self.browser_type,
            max_request_retries=self.config.crawler.max_retries,
            use_incognito_pages=True,
            request_handler_timeout=handler_timeout,
            concurrency_settings=concurrency,
            configuration=crawl_config,
            event_manager=event_manager,
            storage_client=storage_client,
            browser_new_context_options=context_options,
            fingerprint_generator=fingerprint_generator,
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
                    logger.warning(f"Cookie injection failed (appearing as new visitor): {e}")
            # Inject localStorage from profile (anti-bot systems check for GA, segment, etc.)
            ls_entries = self.cookie_jar.get_local_storage(self.base_domain)
            if ls_entries:
                try:
                    ls_json = json.dumps(ls_entries)
                    await page.add_init_script(
                        f"try {{ const _ls = {ls_json}; for (const [k, v] of Object.entries(_ls)) {{ localStorage.setItem(k, v); }} }} catch(e) {{}}"
                    )
                except Exception as e:
                    logger.debug(f"localStorage injection failed: {e}")

            # Attach network listener for compliance data
            self.compliance_collector.attach(page)

            # Anti-detection patches via init script (runs before page JS)
            vp = self.platform.viewport or {"width": 1920, "height": 1080}
            try:
                await page.add_init_script("""
                    // Fix navigator.webdriver (trivial bot signal)
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => false,
                        configurable: true
                    });

                    // Match screen dimensions to viewport (headless mismatch detection)
                    Object.defineProperty(window.screen, 'width', { get: () => """ + str(vp["width"]) + """ });
                    Object.defineProperty(window.screen, 'height', { get: () => """ + str(vp["height"]) + """ });
                    Object.defineProperty(window.screen, 'availWidth', { get: () => """ + str(vp["width"]) + """ });
                    Object.defineProperty(window.screen, 'availHeight', { get: () => """ + str(vp["height"] - 50) + """ });

                    // Block WebRTC IP leak (bypasses proxy, exposes real IP)
                    if (window.RTCPeerConnection) {
                        const OrigRTC = window.RTCPeerConnection;
                        window.RTCPeerConnection = function(...args) {
                            const pc = new OrigRTC(...args);
                            const origCreate = pc.createDataChannel.bind(pc);
                            pc.createDataChannel = function() { return origCreate(...arguments); };
                            // Suppress ICE candidate events that leak local IPs
                            pc.onicecandidate = null;
                            Object.defineProperty(pc, 'onicecandidate', {
                                set: () => {},
                                get: () => null
                            });
                            return pc;
                        };
                        window.RTCPeerConnection.prototype = OrigRTC.prototype;
                    }
                """)
            except Exception as e:
                logger.warning(f"Anti-detection patches failed (bot fingerprint exposed): {e}")

        @crawler.router.default_handler
        async def handle_page(context: PlaywrightCrawlingContext) -> None:
            page = context.page
            url = page.url

            # Skip external pages (but still enqueue same-domain links)
            # Auto-add redirected domains (e.g. bata.in → bata.com)
            page_domain = urlparse(url).netloc.replace("www.", "")
            is_allowed = page_domain in self.allowed_domains
            if not is_allowed:
                # First page redirect: validate brand similarity before trusting.
                # Prevents attacker from hijacking crawler via open redirect.
                if not self.captured_urls and self.pages_captured == 0:
                    base_brand = self.base_domain.split(".")[0]
                    page_brand = page_domain.split(".")[0]
                    if base_brand in page_domain or page_brand in self.base_domain:
                        logger.info(f"Redirect detected: {self.base_domain} → {page_domain}")
                        self.allowed_domains.add(page_domain)
                        is_allowed = True
                    else:
                        logger.warning(
                            f"Redirect to unrelated domain blocked: "
                            f"{self.base_domain} → {page_domain}"
                        )
            if not is_allowed:
                logger.debug(f"Skipping external: {url[:80]}")
                return

            # Skip plain files (markdown, feeds, assets) — not journeys
            url_path = urlparse(url).path.lower()
            if url_path.endswith(SKIP_URL_EXTENSIONS):
                logger.debug(f"Skipping non-page file: {url[:80]}")
                return

            # URL dedup — preserve query params for pages where they matter
            # (search results, product listings, product variants)
            url_type = PageClassifier.classify_url(url)
            if url_type in ("search", "plp", "pdp"):
                normalized = url.split("#")[0].rstrip("/")  # Keep query params
            else:
                normalized = url.split("?")[0].split("#")[0].rstrip("/")
            if normalized in self.captured_urls:
                logger.debug(f"Already captured: {url[:80]}")
                return

            # === READINESS (must run before content checks for JS-rendered SPAs) ===
            try:
                await self.readiness.wait_until_ready(page)
            except Exception as e:
                logger.warning(f"Page readiness wait failed: {e}")

            # Force the declared viewport (ADR p007). browserforge's fingerprint
            # applies a CDP device-metrics override that the context viewport does
            # not win against — page.viewport_size reports the declared size but the
            # real render/screenshot comes out wider (tablet 1280 vs 768). Setting
            # it here (after navigation/fingerprint, before behavior+screenshot)
            # guarantees last-write-wins so screenshots match the declared viewport.
            _vp = self.platform.viewport or {"width": 1920, "height": 1080}
            try:
                await page.set_viewport_size({"width": _vp["width"], "height": _vp["height"]})
            except Exception as e:
                logger.warning(f"set_viewport_size failed: {e}")

            # === OVERLAY DISMISSAL (location/pincode popups, cookie banners) ===
            try:
                await self._dismiss_overlays(page)
            except Exception as e:
                logger.debug(f"Overlay dismissal failed: {e}")

            # Get page info for block/empty detection
            title = await page.title() or ""
            try:
                text_preview = await page.evaluate(
                    "document.body?.innerText?.slice(0, 500) || ''"
                )
            except Exception:
                text_preview = ""

            # Block page detection — DON'T enqueue links from block pages (trap links)
            if AntiCrawlerDetector.is_block_page(title, text_preview):
                self.consecutive_blocks += 1
                logger.warning(
                    f"Block page detected ({self.consecutive_blocks}x): "
                    f"{title[:50]} at {url[:60]}"
                )
                if self.consecutive_blocks >= 3:
                    logger.error("Crawler blocked — 3 consecutive block pages, aborting")
                return

            # Empty page detection
            try:
                html_content = await page.content()
            except Exception:
                html_content = ""

            if AntiCrawlerDetector.is_empty_page(html_content, text_preview):
                logger.warning(f"Empty page detected: {url[:60]}")
                # Still enqueue links from empty pages (SPAs may have nav links)
                try:
                    await context.enqueue_links(strategy="all")
                except Exception as e:
                    logger.debug(f"Link enqueue failed: {e}")
                return

            # Reset consecutive block counter on successful page
            self.consecutive_blocks = 0

            # Enqueue links from valid pages
            # Use "all" strategy because crawlee's same-domain/same-hostname
            # compare against the original request URL, not the current page URL.
            # This breaks on redirects (e.g. bata.in → bata.com). Our handler
            # already filters by allowed_domains so "all" is safe.
            try:
                await context.enqueue_links(strategy="all")
            except Exception as e:
                logger.debug(f"Link enqueue failed: {e}")

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
                self.journey.add_error(url, e, phase="behavior")

            # === ANALYSIS LAYER ===

            # 1. Screenshot with retry (page scrolled to top by behavior sequencer)
            screenshot_path = None
            for _attempt in range(3):
                try:
                    screenshot_path = await self.screenshot_manager.capture_screenshot(
                        page, step_num
                    )
                    break
                except Exception as e:
                    if _attempt == 2:
                        logger.warning(f"Screenshot failed after 3 attempts: {e}")
                        self.journey.add_error(url, e, phase="screenshot")
                    else:
                        logger.debug(f"Screenshot attempt {_attempt + 1} failed, retrying: {e}")
                        await asyncio.sleep(0.5)

            # 2. Parallel data collection (page analysis + compliance + design)
            page_data = {}

            async def _analyze():
                try:
                    return await self.page_analyzer.analyze_page(page)
                except Exception as e:
                    logger.warning(f"Page analysis failed: {e}")
                    self.journey.add_error(url, e, phase="page_analysis")
                    return {"url": url, "title": title}

            async def _compliance():
                try:
                    return await self.compliance_collector.collect(
                        page, context.page.context
                    )
                except Exception as e:
                    logger.warning(f"Compliance data collection failed: {e}")
                    self.journey.add_error(url, e, phase="compliance_collection")
                    return {}

            async def _design():
                try:
                    return await self.design_collector.collect(page)
                except Exception as e:
                    logger.warning(f"Design data collection failed: {e}")
                    self.journey.add_error(url, e, phase="design_collection")
                    return {}

            analysis_result, compliance_data, design_data = await asyncio.gather(
                _analyze(), _compliance(), _design()
            )

            page_data = analysis_result
            page_data["page_type"] = PageClassifier.classify_url(url)

            # Record actual render metrics so consumers/CI can assert that the
            # screenshot matches the declared viewport (corpus v1 defect #5)
            try:
                page_data["device_pixel_ratio"] = await page.evaluate(
                    "window.devicePixelRatio"
                )
                page_data["rendered_viewport"] = page.viewport_size
            except Exception:
                pass

            # Save HTML to file instead of inline (prevents OOM on large sites)
            if page_data.get("html"):
                html_dir = self.output_dir / "html"
                html_dir.mkdir(exist_ok=True)
                html_file = html_dir / f"step-{step_num}.html"
                html_file.write_text(page_data["html"], encoding="utf-8")
                page_data["html_path"] = str(html_file)
                del page_data["html"]
            page_data.update(compliance_data)

            # Merge design data
            page_data["css_variables"] = design_data.get("css_variables", {})
            page_data["component_tree"] = design_data.get("component_tree", [])
            page_data["asset_urls"] = design_data.get("asset_urls", {})
            if isinstance(page_data.get("computed_styles"), list):
                page_data["computed_styles"] = {"text_elements": page_data["computed_styles"]}
            elif not isinstance(page_data.get("computed_styles"), dict):
                page_data["computed_styles"] = {}
            page_data["computed_styles"]["all_elements"] = design_data.get("all_styles", [])

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

            # 6. Build journey step (screenshot_path must be a string per schema)
            step = JourneyStep(
                step_number=step_num,
                url=url,
                title=title,
                screenshot_path=screenshot_path or f"screenshots/step-{step_num:03d}.png",
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

            # 8. Inter-page delay (human pacing)
            delay = self._calculate_page_delay(page_type)
            logger.debug(f"Inter-page delay: {delay:.1f}s ({page_type})")
            await asyncio.sleep(delay)

        # Graceful shutdown: save journey on SIGINT/SIGTERM
        interrupted = False
        loop = asyncio.get_running_loop()
        original_handlers = {}

        def _shutdown_handler(sig, _frame=None):
            nonlocal interrupted
            if interrupted:
                return  # Already handling
            interrupted = True
            logger.warning(f"Received {signal.Signals(sig).name} — saving journey before exit")
            try:
                self.journey.add_error(self.config.base_url, "Crawl interrupted by user", phase="shutdown")
                self.journey.complete()
                journey_file = self.output_dir / "journey.json"
                self.journey.save(str(journey_file))
                logger.info(f"Interrupted journey saved: {self.pages_captured} pages to {journey_file}")
            except Exception as e:
                logger.error(f"Failed to save interrupted journey: {e}")

        for sig in (signal.SIGINT, signal.SIGTERM):
            original_handlers[sig] = signal.getsignal(sig)
            try:
                loop.add_signal_handler(sig, _shutdown_handler, sig)
            except (NotImplementedError, RuntimeError):
                # Windows or nested event loop — fall back to signal.signal
                signal.signal(sig, _shutdown_handler)

        # Run the crawl with all seed URLs (sitemap + base URL)
        try:
            await crawler.run(seed_urls)
        except (KeyboardInterrupt, SystemExit):
            if not interrupted:
                _shutdown_handler(signal.SIGINT)
        finally:
            # Restore original signal handlers
            for sig, handler in original_handlers.items():
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError):
                    pass
                signal.signal(sig, handler or signal.SIG_DFL)

        if interrupted:
            return self.journey

        # Complete journey
        self.journey.complete()

        # Clean up crawlee storage artifacts
        crawlee_storage = Path(storage_dir)
        if crawlee_storage.exists():
            shutil.rmtree(crawlee_storage, ignore_errors=True)
            logger.debug(f"Cleaned up crawlee storage: {storage_dir}")

        logger.info(f"Crawl complete: {self.pages_captured} pages captured")

        return self.journey

    async def _dismiss_overlays(self, page):
        """Dismiss location/pincode popups, cookie consent, and newsletter modals.

        Indian e-commerce sites show a location/delivery-area modal on first
        visit that covers the full viewport and blocks all interaction.
        """
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

        close_selectors = [
            '[class*="AllowLocation" i] [class*="close" i]',
            '[class*="AddressListModal" i] [class*="close" i]',
            '[class*="AddressListModal" i] [class*="dismiss" i]',
            '[class*="location" i][class*="modal" i] [class*="close" i]',
            '[class*="location" i][class*="popup" i] [class*="close" i]',
            '[class*="pincode" i][class*="modal" i] [class*="close" i]',
            '[class*="pincode" i][class*="popup" i] [class*="close" i]',
            '[class*="delivery" i][class*="modal" i] [class*="close" i]',
            '[aria-label*="close" i]',
            'button[class*="close" i]',
            '[class*="modal" i] [class*="close" i]',
            '[class*="popup" i] [class*="close" i]',
        ]
        for selector in close_selectors:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(400)
            except Exception:
                continue

        try:
            removed = await page.evaluate("""() => {
                let n = 0;
                const vw = window.innerWidth, vh = window.innerHeight;
                for (const el of document.querySelectorAll(
                    '[class*="modal" i], [class*="overlay" i], ' +
                    '[class*="backdrop" i], [class*="popup" i]'
                )) {
                    const s = getComputedStyle(el);
                    if (s.position !== 'fixed' && s.position !== 'absolute') continue;
                    if (s.display === 'none' || s.visibility === 'hidden') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= vw * 0.8 && r.height >= vh * 0.8) {
                        el.style.display = 'none';
                        n++;
                    }
                }
                return n;
            }""")
            if removed:
                logger.debug(f"Removed {removed} blocking overlay(s) via JS fallback")
        except Exception:
            pass

    def get_stats(self):
        """Get crawler statistics."""
        return {
            "pages_captured": self.pages_captured,
            "engine": "crawlee",
            "captured_urls": len(self.captured_urls),
            "consecutive_blocks": self.consecutive_blocks,
            "allowed_domains": list(self.allowed_domains),
            "errors": len(self.journey.errors) if self.journey else 0,
        }
