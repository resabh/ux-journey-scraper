"""
Autonomous crawler - main crawl engine that orchestrates all components.

Integrates:
- Navigation queue (priority-based crawling)
- State registry (deduplication)
- Page readiness (accurate captures)
- Element intelligence (smart clickable detection)
- Form filling (checkout flows)
- Auth management (logged-in crawling)
- Human behavior (anti-bot)
- Session splitting support
- Cookie persistence
"""

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ux_journey_scraper.config.scrape_config import PlatformConfig, ScrapeConfig
from ux_journey_scraper.core.auth_manager import AuthManager
from ux_journey_scraper.core.cookie_jar import CookieJar
from ux_journey_scraper.core.element_intelligence import ElementIntelligence
from ux_journey_scraper.core.form_filler import FormFiller
from ux_journey_scraper.core.human_behaviour import HumanBehaviour
from ux_journey_scraper.core.journey_recorder import Journey, JourneyStep
from ux_journey_scraper.core.navigation_behaviour import NavigationBehaviour
from ux_journey_scraper.core.navigation_queue import NavigationAction, NavigationQueue
from ux_journey_scraper.core.compliance_data_collector import ComplianceDataCollector
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.page_readiness import PageReadinessEngine
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager
from ux_journey_scraper.core.session_planner import VisitPlan
from ux_journey_scraper.core.state_registry import StateRegistry
from ux_journey_scraper.core.stealth_browser import create_stealth_browser
from ux_journey_scraper.core.cdp_element_detector import CDPElementDetector
from ux_journey_scraper.core.persistent_queue import PersistentQueue

logger = logging.getLogger(__name__)


class AutonomousCrawler:
    """Autonomous web crawler with intelligent navigation."""

    def __init__(
        self,
        config: ScrapeConfig,
        output_dir: str = "journey_output",
        visit_plan: Optional[VisitPlan] = None,
        cookie_jar: Optional[CookieJar] = None,
        proxy_override: Optional[Dict] = None,
        platform: Optional[PlatformConfig] = None,
        auth_state: Optional[str] = None,
    ):
        """
        Initialize autonomous crawler.

        Args:
            config: Scrape configuration
            output_dir: Output directory for screenshots and data
            visit_plan: Optional visit plan (for session-split mode)
            cookie_jar: Optional cookie jar for persistence across sessions
            proxy_override: Optional proxy config override
            platform: Optional platform config (for continuous mode)
            auth_state: Optional auth state (for continuous mode)
        """
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)

        # Session splitting support
        self.visit_plan = visit_plan
        self.cookie_jar = cookie_jar
        self.proxy_override = proxy_override

        # Platform and auth (for both modes)
        if visit_plan:
            # Split mode: get from visit plan
            self.platform = visit_plan.platform
            self.auth_state = visit_plan.auth_state
            self.max_pages = visit_plan.max_pages
            self.target_page_types = visit_plan.target_page_types
        else:
            # Continuous mode: get from parameters or config
            self.platform = platform or config.platforms[0]
            self.auth_state = auth_state or "logged_out"
            self.max_pages = config.crawler.max_pages
            self.target_page_types = []

        # Derive base domain for internal/external link detection
        self.base_domain = urlparse(config.base_url).netloc.replace("www.", "")

        # Initialize components (with domain awareness)
        self.nav_queue = NavigationQueue(max_depth=config.crawler.max_depth)
        self.nav_queue.base_domain = self.base_domain
        self.state_registry = StateRegistry()
        self.readiness_engine = PageReadinessEngine(
            timeout_ms=config.crawler.timeout_per_page_ms
        )
        self.element_intelligence = ElementIntelligence()
        self.element_intelligence.base_domain = self.base_domain
        self.human = HumanBehaviour()
        self.nav_behaviour = NavigationBehaviour()
        self.screenshot_manager = ScreenshotManager(
            output_dir=self.output_dir / "screenshots",
            blur_pii=True,
        )
        self.page_analyzer = PageAnalyzer()
        self.compliance_collector = ComplianceDataCollector()
        self.auth_manager = AuthManager(config.auth)
        self.form_filler = FormFiller(config.form_fill)
        self.cdp_detector = CDPElementDetector()

        # Use persistent queue if output_dir is available (crash recovery)
        queue_path = self.output_dir / "queue_state.json"
        self.persistent_queue = PersistentQueue(
            str(queue_path), max_depth=config.crawler.max_depth
        )

        # Crawl state
        self.journey: Optional[Journey] = None
        self.current_step = 0
        self.pages_captured = 0
        self.captured_urls: set = set()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def crawl(self) -> Journey:
        """
        Start autonomous crawl.

        Returns:
            Journey object with all captured steps
        """
        session_id = self.visit_plan.session_id if self.visit_plan else "continuous"
        logger.info(f"Starting crawl: {session_id} on {self.platform.type}")
        logger.info(f"Base URL: {self.config.base_url}")
        logger.info(f"Max pages: {self.max_pages}")
        logger.info(f"Auth state: {self.auth_state}")

        # Initialize journey
        self.journey = Journey(
            start_url=self.config.base_url,
            viewport=(
                self.platform.viewport["width"],
                self.platform.viewport["height"],
            ),
            platform_type=self.platform.type,
            user_agent=self.platform.user_agent,
            environment=getattr(self.config, "environment", None),
        )

        try:
            # Launch stealth browser (routes to local or cloud)
            for attempt in range(3):
                try:
                    self.playwright, self.browser, self.context = await create_stealth_browser(
                        config=self.config,
                        platform=self.platform,
                        proxy_override=self.proxy_override,
                    )
                    break
                except Exception as e:
                    if "429" in str(e) and attempt < 2:
                        wait = (attempt + 1) * 30
                        logger.warning(f"Rate limited, waiting {wait}s (attempt {attempt + 1}/3)")
                        await asyncio.sleep(wait)
                    else:
                        raise

            # Inject cookies if available (returning visitor simulation)
            if self.cookie_jar:
                domain = urlparse(self.config.base_url).netloc
                cookies = self.cookie_jar.get(domain)
                if cookies:
                    await self.context.add_cookies(cookies)
                    logger.info(f"Injected {len(cookies)} cookies (returning visitor)")

            self.page = await self.context.new_page()
            self.compliance_collector.attach(self.page)

            # Authenticate if needed
            if self.auth_state == "logged_in":
                auth_success = await self.auth_manager.ensure_authenticated(self.page)
                if not auth_success:
                    raise RuntimeError("Authentication failed")

            # Initialize navigation queue
            entry_url = (
                self.visit_plan.entry_url if self.visit_plan else self.config.base_url
            )
            self.nav_queue.add(
                NavigationAction(
                    type="navigate",
                    priority=100,
                    depth=0,
                    url=entry_url,
                )
            )

            # Add seed URLs with lower priority (explore options)
            for seed_url in self.config.seed_urls:
                if seed_url != entry_url:
                    self.nav_queue.add(
                        NavigationAction(
                            type="navigate",
                            priority=80,
                            depth=0,
                            url=seed_url,
                        )
                    )

            # Main crawl loop
            await self._crawl_loop()

            # Complete journey
            self.journey.complete()

            logger.info(f"Crawl complete: {self.pages_captured} pages captured")

            return self.journey

        finally:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

    # Block page signatures for detection
    BLOCK_SIGNATURES = [
        "access denied", "access is temporarily restricted",
        "403 error", "403 forbidden", "request blocked",
        "captcha", "robot", "unusual activity", "verify you are human",
        "just a moment",  # Cloudflare challenge
    ]

    def _is_block_page(self, title: str, content_preview: str) -> bool:
        """Check if the current page is a block/CAPTCHA page."""
        combined = (title + " " + content_preview).lower()
        return any(sig in combined for sig in self.BLOCK_SIGNATURES)

    async def _crawl_loop(self) -> None:
        """
        Main crawl loop - process navigation queue until empty or limit reached.

        Uses page.goto() for <a> link navigation instead of clicking, which:
        - Works with hidden/accordion/footer links
        - Bypasses Cloudflare challenges on click intercepts
        - Is more reliable than selector-based clicking
        """
        while self.pages_captured < self.max_pages:
            # Get next action
            action = self.nav_queue.next()
            if not action:
                logger.info("Navigation queue exhausted")
                break

            try:
                # Extract metadata for link-based navigation
                href = action.metadata.get("href", "") if action.metadata else ""
                tag = action.metadata.get("tag", "") if action.metadata else ""

                # For <a> tags with href: use page.goto() instead of clicking
                if action.type == "click" and tag == "a" and href and href.startswith("http"):
                    # Skip external links
                    if self.base_domain not in urlparse(href).netloc:
                        continue
                    # Skip non-navigable hrefs
                    if any(href.startswith(p) for p in ["mailto:", "tel:", "javascript:"]):
                        continue

                    logger.info(
                        f"[{self.pages_captured + 1}/{self.max_pages}] "
                        f"Navigating to: {href[:80]}"
                    )
                    try:
                        await self.page.goto(
                            href, timeout=self.config.crawler.timeout_per_page_ms
                        )
                    except Exception as e:
                        error_msg = str(e)
                        if "ERR_TOO_MANY_REDIRECTS" in error_msg:
                            logger.warning(f"Redirect loop detected for {href[:80]}, skipping")
                        elif "ERR_INTERNET_DISCONNECTED" in error_msg:
                            logger.error(f"Network disconnected, stopping crawl")
                            break  # Stop the crawl loop entirely
                        else:
                            logger.warning(f"Navigation failed: {e}")
                        continue

                elif action.type == "navigate":
                    logger.info(
                        f"[{self.pages_captured + 1}/{self.max_pages}] "
                        f"Navigate: {action.url[:80]}"
                    )
                    success = await self._execute_action(action)
                    if not success:
                        continue

                elif action.type == "click":
                    # Non-link clicks (buttons etc) — use original click logic
                    logger.info(
                        f"[{self.pages_captured + 1}/{self.max_pages}] "
                        f"Click: {action.selector}"
                    )
                    success = await self._execute_action(action)
                    if not success:
                        continue
                else:
                    continue

                # Wait for page readiness
                ready = await self.readiness_engine.wait_until_ready(self.page)
                if not ready:
                    logger.warning("Page readiness timeout, capturing anyway")

                # Detect block pages before capturing
                try:
                    title = await self.page.title() or ""
                    preview = await self.page.evaluate(
                        "document.body?.innerText?.slice(0, 500) || ''"
                    )
                    if self._is_block_page(title, preview):
                        logger.warning(f"Block page detected: {title}")
                        try:
                            await self.page.go_back()
                            await asyncio.sleep(1)
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass

                # Check for auth wall
                if await self.auth_manager.detect_auth_wall(self.page):
                    logger.warning("Auth wall detected, attempting recovery")
                    recovered = await self.auth_manager.recover_from_auth_wall(
                        self.page
                    )
                    if not recovered:
                        logger.error("Auth recovery failed, skipping page")
                        continue

                # Get current state
                dom_content = await self.page.content()
                current_url = self.page.url

                # Check if this is a new state
                if not self.state_registry.is_new_state(current_url, dom_content):
                    logger.debug("Duplicate state detected, skipping capture")
                    continue

                # URL-based dedup (catches same URL with different DOM)
                normalized_url = self.state_registry.normalize_url(current_url)
                if normalized_url in self.captured_urls:
                    logger.debug("URL already captured, skipping")
                    continue

                # Capture screen
                await self._capture_screen()
                self.pages_captured += 1
                self.captured_urls.add(normalized_url)

                # Save queue state for crash recovery
                try:
                    self.persistent_queue._queue = self.nav_queue
                    self.persistent_queue.mark_completed(action)
                except Exception:
                    pass

                # Enqueue all <a href> links from page (more reliable than clickable detection)
                link_count = await self._enqueue_page_links(self.page, action.depth)
                logger.info(f"Enqueued {link_count} page links")

                # Analyze page type for navigation behaviour
                page_data = await self.page_analyzer.analyze_page(self.page)
                page_type = page_data.get("page_type", "unknown")

                # Simulate realistic page reading time
                await self.nav_behaviour.simulate_page_reading(self.page, page_type)

                # Fill forms if present (gated by config)
                if self.config.form_fill.enabled:
                    fill_result = await self.form_filler.fill_all_forms(self.page)
                    if fill_result["fields_filled"] > 0:
                        logger.info(f"Filled {fill_result['fields_filled']} form fields")

                        # If form filled, wait and capture updated state
                        await asyncio.sleep(1)
                        if self.state_registry.is_new_state(
                            current_url, await self.page.content()
                        ):
                            await self._capture_screen()
                            self.pages_captured += 1

                # Random browsing behaviors (anti-detection)
                await self.nav_behaviour.maybe_backtrack(self.page)
                await self.nav_behaviour.maybe_revisit_home(
                    self.page, self.config.base_url
                )
                await self.nav_behaviour.maybe_scroll_without_clicking(self.page)
                await self.nav_behaviour.maybe_long_pause()

                # Find new clickables (standard + CDP framework detection)
                try:
                    clickables = await self.element_intelligence.find_all_clickables(
                        self.page,
                        in_viewport_only=False,
                        max_elements=100,
                    )

                    # Also detect framework-level event listeners (React/Vue/Angular)
                    try:
                        cdp_elements = await self.cdp_detector.find_elements_with_listeners(
                            self.page
                        )
                        if cdp_elements:
                            logger.debug(
                                f"CDP detected {len(cdp_elements)} framework elements"
                            )
                            for el in cdp_elements:
                                clickables.append({
                                    **el,
                                    "priority": 25,  # Base priority for CDP-detected
                                    "href": "",
                                    "class": "",
                                })
                    except Exception as e:
                        logger.debug(f"CDP detection skipped: {e}")

                    # Add clickables to navigation queue
                    added = 0
                    for clickable in clickables:
                        new_action = NavigationAction(
                            type="click",
                            priority=clickable["priority"],
                            depth=action.depth + 1,
                            url=current_url,
                            selector=self._generate_selector(clickable),
                            metadata=clickable,
                        )

                        if self.nav_queue.add(new_action):
                            added += 1

                    logger.debug(f"Added {added} new actions to queue")
                except Exception as e:
                    logger.error(f"Error finding clickables: {e}")

                # Human delay before next action
                await self.human.human_delay(
                    self.config.crawler.delay_ms,
                    self.config.crawler.delay_ms + self.config.crawler.delay_jitter_ms,
                    reason="page_load",
                )

            except Exception as e:
                logger.error(f"Error processing action: {e}")
                continue

    async def _enqueue_page_links(self, page, current_depth: int) -> int:
        """Extract all <a href> links from page and add as navigate actions."""
        try:
            links = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(href => href && href.startsWith('http'))
            """)
            added = 0
            for href in links:
                parsed = urlparse(href)
                # Skip external, mailto, tel, javascript
                if self.base_domain not in parsed.netloc:
                    continue
                if any(href.startswith(p) for p in ["mailto:", "tel:", "javascript:"]):
                    continue
                action = NavigationAction(
                    type="navigate",
                    priority=30,  # Default priority for discovered links
                    depth=current_depth + 1,
                    url=href,
                )
                if self.nav_queue.add(action):
                    added += 1
            return added
        except Exception as e:
            logger.error(f"Error enqueuing page links: {e}")
            return 0

    async def _execute_action(self, action: NavigationAction) -> bool:
        """
        Execute a navigation action.

        Args:
            action: NavigationAction to execute

        Returns:
            True if action succeeded
        """
        try:
            if action.type == "navigate":
                await self.page.goto(
                    action.url, timeout=self.config.crawler.timeout_per_page_ms
                )
                return True

            elif action.type == "click":
                # Get element center for human click
                center = await self.element_intelligence.get_element_center(
                    self.page, action.selector
                )

                if not center:
                    logger.warning(f"Element not found: {action.selector}")
                    return False

                # Human-like click
                await self.human.human_click(self.page, center[0], center[1])
                return True

            else:
                logger.warning(f"Unknown action type: {action.type}")
                return False

        except Exception as e:
            logger.error(f"Action execution error: {e}")
            return False

    async def _capture_screen(self) -> None:
        """Capture current page state as a journey step."""
        self.current_step += 1

        logger.debug(f"Capturing screen {self.current_step}...")

        try:
            # Capture screenshot
            screenshot_path = await self.screenshot_manager.capture_screenshot(
                self.page, self.current_step
            )

            # Analyze page
            page_data = await self.page_analyzer.analyze_page(self.page)

            # Capture browser-state data for compliance analysis
            try:
                compliance_data = await self.compliance_collector.collect(self.page, self.context)
                page_data.update(compliance_data)
            except Exception as e:
                logger.debug(f"Compliance data collection failed: {e}")

            # Create step
            step = JourneyStep(
                step_number=self.current_step,
                url=self.page.url,
                title=await self.page.title(),
                screenshot_path=screenshot_path,
                page_data=page_data,
            )

            # Add to journey
            self.journey.add_step(step)

            logger.info(f"✓ Step {self.current_step} captured: {step.title}")

        except Exception as e:
            logger.error(f"Error capturing screen: {e}")

    def _generate_selector(self, clickable: Dict) -> str:
        """
        Generate a robust CSS selector for a clickable element.

        Handles Tailwind CSS classes (h-[38px], bg-[#5430B2], md:w-[275px])
        which contain brackets, colons, and hashes that crash querySelectorAll.

        Strategy: href for links, text for buttons, aria-label, safe classes.
        """
        el_id = clickable.get("id", "")
        tag = clickable.get("tag", "div")
        href = clickable.get("href", "")
        classes = clickable.get("class", "")

        # 1. ID-based (escape if needed)
        if el_id:
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', el_id):
                return f"#{el_id}"
            else:
                return f'[id="{el_id}"]'

        # 2. For <a> tags, use href (most reliable for navigation)
        if tag == "a" and href and href != "#":
            safe_href = href.replace('"', '\\"')
            if len(safe_href) > 100:
                safe_href = safe_href[:100]
                return f'{tag}[href^="{safe_href}"]'
            return f'{tag}[href="{safe_href}"]'

        # 3. For buttons, use text content (Playwright-style)
        text = clickable.get("text", "").strip()
        if tag == "button" and text:
            safe_text = text[:50].replace('"', '\\"')
            return f'button:has-text("{safe_text}")'

        # 4. Use aria-label if available
        aria = clickable.get("aria_label", "")
        if aria:
            safe_aria = aria.replace('"', '\\"')
            return f'[aria-label="{safe_aria}"]'

        # 5. Use first safe CSS class (no Tailwind brackets/colons/hashes)
        if classes and isinstance(classes, str):
            for cls in classes.split():
                if re.match(r'^[a-zA-Z_][a-zA-Z0-9_-]*$', cls):
                    return f"{tag}.{cls}"

        # 6. Fallback to tag only
        return tag

    def get_stats(self) -> Dict:
        """
        Get crawler statistics.

        Returns:
            Dictionary with stats
        """
        return {
            "pages_captured": self.pages_captured,
            "current_step": self.current_step,
            "queue_stats": self.nav_queue.get_stats(),
            "state_stats": self.state_registry.get_stats(),
        }

    async def get_cookies(self) -> List[Dict]:
        """
        Get current cookies from browser context.
        Used by CrawlOrchestrator to persist cookies across sessions.

        Returns:
            List of cookie dicts
        """
        if not self.context:
            return []

        try:
            cookies = await self.context.cookies()
            return cookies
        except Exception as e:
            logger.warning(f"Failed to get cookies: {e}")
            return []
