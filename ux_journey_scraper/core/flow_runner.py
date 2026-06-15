"""Directed journey flows — actively COMPLETE critical journeys.

Corpus v1 showed that link-following alone never captures the journeys that
matter most: cart-with-items, checkout-start, search-results, login. This
module drives a real browser through those flows:

    PDP → click add-to-cart → /cart (with items) → click checkout (capture
    checkout-start and STOP — no payment details are ever submitted, no order
    is ever placed), plus search→results and login/account page captures.

Each captured step is tagged with page_data["flow"] so the coverage reporter
can distinguish a completed flow (e.g. a cart that actually has items) from a
page that merely matched a URL pattern.
"""

import logging
import re
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright

from ux_journey_scraper.core.journey_recorder import Journey, JourneyStep
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.page_classifier import PageClassifier
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager

logger = logging.getLogger(__name__)

ADD_TO_CART_SELECTORS = [
    'button[name="add"]',  # Shopify default
    'form[action*="/cart/add"] button[type="submit"]',
    'form[action*="/cart/add"] [type="submit"]',
    '[data-action*="add-to-cart" i]',
    "button#AddToCart",
    'button[class*="add-to-cart" i]',
    'button[id*="add-to-cart" i]',
]

ADD_TO_CART_TEXT = re.compile(r"add to (cart|bag|basket)", re.I)

CHECKOUT_SELECTORS = [
    'button[name="checkout"]',
    'input[name="checkout"]',
    'button[id*="checkout" i]',
    'a[href*="/checkout"]',
]

CHECKOUT_TEXT = re.compile(r"^\s*(proceed to\s+)?check\s?out\s*$", re.I)

CART_ITEM_SELECTORS = [
    "[data-cart-item]",
    ".cart-item",
    ".cart__item",
    '[class*="cart-item" i]',
    '[class*="cart__item" i]',
    '[class*="line-item" i]',
    'form[action="/cart"] [class*="item" i]',
]

EMPTY_CART_TEXT = re.compile(
    r"(cart|bag|basket)\s+is\s+(currently\s+)?empty|nothing in your (cart|bag)", re.I
)

LOGIN_PATHS = ["/account/login", "/login", "/account", "/customer/login"]

CART_PATHS = ["/cart", "/bag", "/basket"]


class FlowRunner:
    """Drive a browser through critical journeys and record each step."""

    def __init__(
        self,
        config,
        output_dir: str,
        platform,
        browser_type: str = "webkit",
        cookie_jar=None,
    ):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.platform = platform
        self.browser_type = browser_type
        self.cookie_jar = cookie_jar
        self.base_url = config.base_url.rstrip("/")
        self.base_domain = urlparse(config.base_url).netloc.replace("www.", "")

        self.screenshot_manager = ScreenshotManager(
            output_dir=self.output_dir / "screenshots", blur_pii=True
        )
        self.page_analyzer = PageAnalyzer()

        vp = platform.viewport or {"width": 1920, "height": 1080}
        self.viewport = vp
        self.journey = Journey(
            start_url=config.base_url,
            viewport=(vp["width"], vp["height"]),
            platform_type=platform.type,
            user_agent=platform.user_agent,
        )
        self.step_num = 0

    async def run(self, pdp_urls=None, search_term: str = "headphones") -> Journey:
        """Execute all directed flows. Each flow is independent — one failing
        does not abort the others.

        Args:
            pdp_urls: Candidate product-page URLs discovered by the crawl.
            search_term: Query for the search→results flow.

        Returns:
            Journey with flow-tagged steps.
        """
        pdp_urls = list(pdp_urls or [])
        logger.info(
            f"FlowRunner starting on {self.platform.type}: "
            f"{len(pdp_urls)} candidate PDPs, search term {search_term!r}"
        )

        async with async_playwright() as p:
            browser_launcher = getattr(p, self.browser_type, p.chromium)
            browser = await browser_launcher.launch(headless=self.config.crawler.headless)
            context_kwargs = {
                "viewport": {
                    "width": self.viewport["width"],
                    "height": self.viewport["height"],
                },
                "locale": getattr(self.platform, "locale", None) or "en-IN",
                "timezone_id": getattr(self.platform, "timezone_id", None) or "Asia/Kolkata",
            }
            if self.platform.user_agent:
                context_kwargs["user_agent"] = self.platform.user_agent
            if self.platform.type in ("web_mobile", "web_tablet"):
                context_kwargs["is_mobile"] = self.browser_type != "firefox"
                context_kwargs["has_touch"] = True
                context_kwargs["device_scale_factor"] = 2

            context = await browser.new_context(**context_kwargs)

            # Anti-detection patches — same as crawlee_adapter's pre_navigation_hook
            vp = self.viewport
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => false, configurable: true
                });
                Object.defineProperty(window.screen, 'width', { get: () => """
                + str(vp["width"])
                + """ });
                Object.defineProperty(window.screen, 'height', { get: () => """
                + str(vp["height"])
                + """ });
                Object.defineProperty(window.screen, 'availWidth', { get: () => """
                + str(vp["width"])
                + """ });
                Object.defineProperty(window.screen, 'availHeight', { get: () => """
                + str(vp["height"] - 50)
                + """ });
                if (window.RTCPeerConnection) {
                    const OrigRTC = window.RTCPeerConnection;
                    window.RTCPeerConnection = function(...args) {
                        const pc = new OrigRTC(...args);
                        pc.onicecandidate = null;
                        Object.defineProperty(pc, 'onicecandidate', {
                            set: () => {}, get: () => null
                        });
                        return pc;
                    };
                    window.RTCPeerConnection.prototype = OrigRTC.prototype;
                }
                """
            )

            if self.cookie_jar:
                cookies = self.cookie_jar.get(self.base_domain)
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception as e:
                        logger.debug(f"Cookie injection failed: {e}")

            page = await context.new_page()

            cart_ready = False
            try:
                cart_ready = await self._flow_add_to_cart(page, pdp_urls)
            except Exception as e:
                logger.warning(f"add-to-cart flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_add_to_cart")

            try:
                await self._flow_cart_and_checkout(page, cart_ready)
            except Exception as e:
                logger.warning(f"cart/checkout flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_checkout")

            try:
                await self._flow_search(page, search_term)
            except Exception as e:
                logger.warning(f"search flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_search")

            try:
                await self._flow_login(page)
            except Exception as e:
                logger.warning(f"login flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_login")

            try:
                await self._flow_site_specific(page)
            except Exception as e:
                logger.warning(f"site-specific flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_site_specific")

            # Harvest cookies back into the jar (keeps the session persona)
            if self.cookie_jar:
                try:
                    harvested = await context.cookies()
                    if harvested:
                        self.cookie_jar.update(self.base_domain, harvested)
                except Exception as e:
                    logger.debug(f"Cookie harvest failed: {e}")

            await browser.close()

        self.journey.complete()
        flows = [(s.page_data or {}).get("flow") for s in self.journey.steps]
        logger.info(f"FlowRunner done: {len(self.journey.steps)} steps, flows={flows}")
        return self.journey

    # ----- individual flows -------------------------------------------------

    async def _flow_add_to_cart(self, page, pdp_urls) -> bool:
        """Open a PDP and click add-to-cart. Returns True if an item was added."""
        candidates = pdp_urls[:4]
        if not candidates:
            # No PDPs discovered — try to find one from the homepage
            await self._goto(page, self.base_url)
            hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            candidates = [h for h in hrefs if PageClassifier.classify_url(h) == "pdp"][:4]

        for url in candidates:
            if not await self._goto(page, url):
                continue
            await self._dismiss_popups(page)
            await self._capture(page, flow="pdp_view")

            button = await self._find_add_to_cart(page)
            if button is None:
                logger.info(f"No add-to-cart control on {url[:80]}")
                continue
            try:
                await button.click(timeout=8000)
            except Exception as e:
                logger.info(f"add-to-cart click failed on {url[:80]}: {e}")
                continue
            await page.wait_for_timeout(3500)  # cart drawer / ajax add
            await self._capture(page, flow="add_to_cart")
            logger.info(f"Added to cart from {url[:80]}")
            return True

        logger.warning("add-to-cart: no PDP produced a clickable add button")
        return False

    async def _flow_cart_and_checkout(self, page, cart_ready: bool):
        """Open the cart, verify items, then click through to checkout-start.

        Stops at the first checkout page. Never fills payment details, never
        places an order.
        """
        cart_loaded = False
        for path in CART_PATHS:
            if await self._goto(page, self.base_url + path):
                if "404" not in (await page.title()).lower():
                    cart_loaded = True
                    break
        if not cart_loaded:
            logger.warning("cart flow: no cart page reachable")
            return

        await self._dismiss_popups(page)
        item_count = await self._count_cart_items(page)
        body_text = await self._body_text(page)
        looks_empty = bool(EMPTY_CART_TEXT.search(body_text)) and item_count == 0

        flow = (
            "cart_with_items"
            if (item_count > 0 or (cart_ready and not looks_empty))
            else "cart_empty"
        )
        await self._capture(page, flow=flow, extra={"cart_item_count": item_count})
        logger.info(f"Cart captured: {flow} ({item_count} items detected)")

        if flow != "cart_with_items":
            return

        # Checkout-start: click the checkout control (fall back to /checkout)
        clicked = False
        for selector in CHECKOUT_SELECTORS:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=8000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            try:
                btn = page.get_by_role("button", name=CHECKOUT_TEXT).first
                if await btn.count():
                    await btn.click(timeout=8000)
                    clicked = True
            except Exception:
                pass
        if not clicked:
            await self._goto(page, self.base_url + "/checkout")

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(4000)

        url = page.url.lower()
        body_text = (await self._body_text(page)).lower()
        on_checkout = "checkout" in url or any(
            marker in body_text
            for marker in (
                "contact information",
                "shipping address",
                "delivery address",
                "payment method",
            )
        )
        if on_checkout:
            await self._capture(page, flow="checkout_start")
            logger.info(f"Checkout-start captured at {page.url[:80]} — stopping here")
        else:
            logger.warning(f"Checkout did not load (still at {page.url[:80]})")

    async def _flow_search(self, page, term: str):
        """Capture a search-results page for the configured term."""
        search_urls = [
            f"{self.base_url}/search?q={quote_plus(term)}",  # Shopify & most platforms
            f"{self.base_url}/search?query={quote_plus(term)}",
        ]
        for url in search_urls:
            if not await self._goto(page, url):
                continue
            title = (await page.title()).lower()
            if "404" in title or "not found" in title:
                continue
            await self._dismiss_popups(page)
            await self._capture(page, flow="search_results", page_type="search")
            logger.info(f"Search results captured for {term!r}")
            return

        # Fall back to typing into a search box on the homepage
        if await self._goto(page, self.base_url):
            await self._dismiss_popups(page)
            for selector in ('input[type="search"]', 'input[name="q"]', '[role="searchbox"]'):
                box = page.locator(selector).first
                try:
                    if await box.count() and await box.is_visible():
                        await box.fill(term)
                        await box.press("Enter")
                        await page.wait_for_timeout(4000)
                        await self._capture(page, flow="search_results", page_type="search")
                        logger.info(f"Search results captured via search box for {term!r}")
                        return
                except Exception:
                    continue
        logger.warning("search flow: could not reach a results page")

    async def _flow_login(self, page):
        """Capture the login/account entry page (no credentials submitted)."""
        for path in LOGIN_PATHS:
            if not await self._goto(page, self.base_url + path):
                continue
            title = (await page.title()).lower()
            if "404" in title or "not found" in title:
                continue
            await self._dismiss_popups(page)
            await self._capture(page, flow="login_page", page_type="account")
            logger.info(f"Login page captured at {page.url[:80]}")
            return
        logger.warning("login flow: no login page reachable")

    async def _flow_site_specific(self, page):
        """Visit site-specific journey URLs from the coverage config."""
        site_journeys = getattr(getattr(self.config, "coverage", None), "site_journeys", [])
        for entry in site_journeys:
            url = entry.get("url")
            if not url:
                continue
            if not await self._goto(page, url):
                continue
            title = (await page.title()).lower()
            if "404" in title or "not found" in title:
                logger.info(f"site journey {entry.get('id')}: 404 at {url}")
                continue
            await self._dismiss_popups(page)
            await self._capture(page, flow=entry.get("id", "site_specific"))
            logger.info(f"Site journey captured: {entry.get('id')} at {url[:80]}")

    # ----- helpers ----------------------------------------------------------

    async def _goto(self, page, url: str) -> bool:
        try:
            response = await page.goto(
                url, timeout=45000, wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(2500)
            if response is not None and response.status >= 400:
                logger.info(f"goto {url[:80]} → HTTP {response.status}")
                return False
            return True
        except Exception as e:
            logger.info(f"goto failed {url[:80]}: {e}")
            return False

    async def _find_add_to_cart(self, page):
        for selector in ADD_TO_CART_SELECTORS:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible() and await loc.is_enabled():
                    return loc
            except Exception:
                continue
        try:
            loc = page.get_by_role("button", name=ADD_TO_CART_TEXT).first
            if await loc.count() and await loc.is_visible():
                return loc
        except Exception:
            pass
        return None

    async def _count_cart_items(self, page) -> int:
        best = 0
        for selector in CART_ITEM_SELECTORS:
            try:
                n = await page.locator(selector).count()
                best = max(best, n)
            except Exception:
                continue
        return best

    async def _body_text(self, page) -> str:
        try:
            return await page.evaluate("document.body?.innerText?.slice(0, 5000) || ''")
        except Exception:
            return ""

    async def _dismiss_popups(self, page):
        """Best-effort close of newsletter/consent overlays."""
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        for selector in (
            '[aria-label*="close" i]',
            'button[class*="close" i]',
            '[class*="modal" i] [class*="close" i]',
        ):
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(400)
            except Exception:
                continue

    async def _capture(self, page, flow: str, page_type: str = None, extra: dict = None):
        """Record the current page as a journey step tagged with the flow id."""
        self.step_num += 1
        url = page.url
        title = await page.title()

        screenshot_path = None
        try:
            screenshot_path = await self.screenshot_manager.capture_screenshot(page, self.step_num)
        except Exception as e:
            logger.warning(f"flow screenshot failed: {e}")
            self.journey.add_error(url, e, phase="flow_screenshot")

        try:
            page_data = await self.page_analyzer.analyze_page(page)
        except Exception as e:
            logger.warning(f"flow page analysis failed: {e}")
            self.journey.add_error(url, e, phase="flow_page_analysis")
            page_data = {"url": url, "title": title}

        page_data["page_type"] = page_type or PageClassifier.classify_url(url)
        page_data["flow"] = flow
        if extra:
            page_data.update(extra)
        try:
            page_data["device_pixel_ratio"] = await page.evaluate("window.devicePixelRatio")
            page_data["rendered_viewport"] = page.viewport_size
        except Exception:
            pass

        # Externalize HTML like the main crawler does
        if page_data.get("html"):
            html_dir = self.output_dir / "html"
            html_dir.mkdir(exist_ok=True)
            html_file = html_dir / f"step-{self.step_num}.html"
            html_file.write_text(page_data["html"], encoding="utf-8")
            page_data["html_path"] = str(html_file)
            del page_data["html"]

        self.journey.add_step(
            JourneyStep(
                step_number=self.step_num,
                url=url,
                title=title,
                screenshot_path=screenshot_path,
                page_data=page_data,
            )
        )
