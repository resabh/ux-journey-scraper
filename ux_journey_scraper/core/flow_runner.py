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

import json
import logging
import re
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from playwright.async_api import async_playwright

from ux_journey_scraper.core.anti_crawler_detector import AntiCrawlerDetector
from ux_journey_scraper.core.journey_recorder import Journey, JourneyStep
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.page_classifier import PageClassifier
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager

logger = logging.getLogger(__name__)

ADD_TO_CART_SELECTORS = [
    "#freebie-add-to-cart",  # boAt freebie upsell system (visible <a>)
    "a.freebie-atc-button",
    'button[name="add"]',  # Shopify default
    'form[action*="/cart/add"] button[type="submit"]',
    'form[action*="/cart/add"] [type="submit"]',
    '[data-action*="add-to-cart" i]',
    "button#AddToCart",
    'button[class*="add-to-cart" i]',
    'button[id*="add-to-cart" i]',
    'a[id*="add-to-cart" i]',
    'a[class*="add-to-cart" i]',
]

VARIANT_SELECTORS = [
    "label.color-swatch__item",
    '.swatch-element label',
    '[data-option-index] label',
    '.product-form__input label',
    'input[type="radio"][name*="option"]',
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
            output_dir=self.output_dir / "screenshots",
            blur_pii=config.crawler.screenshot_blur_pii,
        )
        self.page_analyzer = PageAnalyzer()

        vp = platform.viewport or {"width": 1920, "height": 1080}
        self.viewport = vp
        self.journey = Journey(
            start_url=config.base_url,
            viewport=(vp["width"], vp["height"]),
            platform_type=platform.type,
            user_agent=platform.user_agent,
            environment=getattr(config, "environment", None),
        )
        self.step_num = 0
        # Track the most recent navigation response for per-step metadata
        self._last_response_status = None

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

            # Location precondition: inject location cookies BEFORE any navigation
            if self.config.location.cookies_file:
                try:
                    with open(self.config.location.cookies_file, "r") as f:
                        loc_cookies = json.load(f)
                    await context.add_cookies(loc_cookies)
                    logger.debug(f"Location cookies injected from {self.config.location.cookies_file}")
                except Exception as e:
                    logger.warning(f"Location cookie injection failed: {e}")

            if self.cookie_jar:
                cookies = self.cookie_jar.get(self.base_domain)
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception as e:
                        logger.debug(f"Cookie injection failed: {e}")

            page = await context.new_page()

            # Programmatic location solve for hyperlocal sites
            if self.config.location.pincode:
                await self._solve_location(page)

            try:
                await self._flow_homepage_and_plp(page)
            except Exception as e:
                logger.warning(f"homepage/plp flow failed: {e}")
                self.journey.add_error(self.base_url, e, phase="flow_homepage_plp")

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

    async def _flow_homepage_and_plp(self, page):
        """Capture homepage and PLP seed URLs to fill coverage gaps."""
        for url in self.config.seed_urls:
            url_type = PageClassifier.classify_url(url)
            if url_type in ("homepage", "plp"):
                if await self._goto(page, url):
                    await self._dismiss_popups(page)
                    await page.wait_for_timeout(1500)
                    await self._capture(page, flow=url_type, page_type=url_type)
                    logger.info(f"{url_type.upper()} captured: {url[:80]}")

    async def _discover_pdp_via_click(self, page, search_term: str) -> list:
        """Discover PDP URLs from search/PLP pages.

        Three strategies, tried in order:
        1. <a href> links with PDP patterns (standard sites)
        2. Embedded product URLs in SPA state (React/Next hydration data)
        3. Click product cards and check for SPA navigation
        """
        search_url = f"{self.base_url}/search?q={quote_plus(search_term)}"
        if not await self._goto(page, search_url):
            search_url = f"{self.base_url}/products?q={quote_plus(search_term)}"
            if not await self._goto(page, search_url):
                return []
        await self._dismiss_popups(page)
        await page.wait_for_timeout(2000)

        # Strategy 1: standard <a> links
        pdp_urls = await page.evaluate("""() => {
            const cards = document.querySelectorAll(
                'a[href*="/p/"], a[href*="/product/"], a[href*="/products/"], a[href*="/dp/"]'
            );
            return [...new Set([...cards].map(a => a.href))].slice(0, 5);
        }""")
        if pdp_urls:
            return pdp_urls

        # Strategy 2: extract product URLs from SPA state (React SSR hydration)
        pdp_urls = await page.evaluate(r"""() => {
            const html = document.documentElement.outerHTML;
            const re = /\\u002F(?:product|p|dp|item)\\u002F([\w-]+)/g;
            const found = new Set();
            let m;
            while ((m = re.exec(html)) !== null) {
                found.add('/product/' + m[1]);
            }
            if (found.size === 0) {
                const re2 = /["'](\/(?:product|p|dp|item)\/[\w-]+)["']/g;
                while ((m = re2.exec(html)) !== null) {
                    found.add(m[1]);
                }
            }
            return [...found].slice(0, 5).map(p => location.origin + p);
        }""")
        if pdp_urls:
            logger.info(f"Discovered {len(pdp_urls)} PDP URLs from SPA state")
            return pdp_urls

        # Strategy 3: click product cards
        before_url = page.url
        clicked = await page.evaluate("""() => {
            const priceEls = [];
            for (const el of document.querySelectorAll('div, li, article')) {
                const r = el.getBoundingClientRect();
                if (r.width < 80 || r.height < 80 || r.top < 0 || r.top > 2000) continue;
                const text = el.innerText || '';
                if ((text.includes('₹') || text.includes('Rs')) && text.length < 300) {
                    priceEls.push({x: r.x + r.width / 2, y: r.y + r.height / 2});
                }
            }
            return priceEls.slice(0, 3);
        }""")

        for coord in (clicked or []):
            try:
                await page.mouse.click(coord["x"], coord["y"])
                await page.wait_for_timeout(4000)
                new_url = page.url
                if new_url != before_url and PageClassifier.classify_url(new_url) == "pdp":
                    logger.info(f"Discovered PDP via card click: {new_url[:80]}")
                    return [new_url]
                if new_url != before_url:
                    await page.go_back()
                    await page.wait_for_timeout(2000)
            except Exception:
                continue

        return []

    _OOS_PATTERN = re.compile(
        r"sold\s*out|out\s*of\s*stock|notify\s*me|unavailable|coming\s*soon",
        re.IGNORECASE,
    )

    async def _check_pdp_availability(self, page) -> str:
        """Tri-state availability check: in_stock / out_of_stock / unknown."""
        button = await self._find_add_to_cart(page)
        if button is None:
            return "unknown"

        try:
            is_enabled = await button.is_enabled()
            disabled_attr = await button.get_attribute("disabled")
            aria_disabled = await button.get_attribute("aria-disabled")
            text = (await button.inner_text()).strip()
        except Exception:
            return "unknown"

        if disabled_attr is not None or aria_disabled == "true" or not is_enabled:
            return "out_of_stock"
        if self._OOS_PATTERN.search(text):
            return "out_of_stock"
        return "in_stock"

    async def _flow_add_to_cart(self, page, pdp_urls) -> bool:
        """Open a PDP and click add-to-cart. Returns True if an item was added."""
        candidates = pdp_urls[:10]
        if not candidates:
            await self._goto(page, self.base_url)
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.href)"
            )
            candidates = [
                h for h in hrefs if PageClassifier.classify_url(h) == "pdp"
            ][:10]

        if not candidates:
            search_term = getattr(
                getattr(self.config, "coverage", None), "search_term", "product"
            )
            candidates = await self._discover_pdp_via_click(page, search_term)

        first_oos_url = None
        for url in candidates:
            if not await self._goto(page, url):
                continue
            await self._dismiss_popups(page)

            availability = await self._check_pdp_availability(page)
            if availability == "out_of_stock":
                logger.info(f"PDP out-of-stock: {url[:80]}")
                if first_oos_url is None:
                    first_oos_url = url
                continue
            if availability == "unknown" and first_oos_url is not None:
                continue

            await self._capture(
                page, flow="pdp_view",
                extra={"availability": availability},
            )

            button = await self._find_add_to_cart(page)
            if button is None:
                logger.info(f"No add-to-cart control on {url[:80]}")
                continue
            try:
                await button.click(timeout=8000)
            except Exception as e:
                logger.info(f"add-to-cart click failed on {url[:80]}: {e}")
                continue
            await page.wait_for_timeout(3500)
            await self._capture(page, flow="add_to_cart")
            logger.info(f"Added to cart from {url[:80]}")
            return True

        # All candidates exhausted — capture one OOS fallback if available
        if first_oos_url:
            if await self._goto(page, first_oos_url):
                await self._dismiss_popups(page)
                await self._capture(
                    page, flow="pdp_view",
                    extra={"availability": "out_of_stock"},
                )
                self.journey.add_error(
                    first_oos_url,
                    Exception("All PDP candidates out of stock"),
                    phase="flow_add_to_cart",
                )

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
            f"{self.base_url}/products?q={quote_plus(term)}",  # JioMart SPA
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

    # ----- location solve ----------------------------------------------------

    async def _solve_location(self, page):
        """Programmatic location solve for hyperlocal sites.

        Navigates to base_url, detects the address panel, and completes the
        location flow via Google Places autocomplete. Persists resulting
        cookies back to cookies_file so subsequent sessions can skip the solve.
        Always verifies via PDP load regardless of whether the fill was needed.
        """
        pincode = self.config.location.pincode
        if not pincode:
            return

        if not await self._goto(page, self.base_url):
            logger.warning("Location solve: could not load base URL")
            return
        await page.wait_for_timeout(2000)

        # Check if location panel is visible (needs fill)
        panel_visible = False
        for panel_selector in (
            '[class*="AddressFullscreenModal" i]',
            '[class*="address-modal" i]',
            '[class*="location-modal" i]',
            '[class*="pincode-modal" i]',
        ):
            try:
                loc = page.locator(panel_selector).first
                if await loc.count() and await loc.is_visible():
                    panel_visible = True
                    break
            except Exception:
                continue

        if not panel_visible:
            try:
                select_loc = page.get_by_text("Select Location Manually")
                if await select_loc.count() and await select_loc.is_visible():
                    panel_visible = True
            except Exception:
                pass

        if panel_visible:
            await self._fill_location(page, pincode)
        else:
            logger.info("Location solve: no address panel detected — location may already be set via cookies")

        # Always verify via PDP load — whether fill was needed or cookies sufficed
        await self._verify_location_pdp(page)

    async def _fill_location(self, page, pincode):
        """Fill the location modal with pincode and confirm."""
        # Step 1: Click "Select Location Manually"
        try:
            manual_btn = page.get_by_text("Select Location Manually")
            if await manual_btn.count() and await manual_btn.is_visible():
                await manual_btn.click(timeout=5000)
                await page.wait_for_timeout(1500)
        except Exception as e:
            logger.debug(f"Location solve: 'Select Location Manually' click: {e}")

        # Step 2: Type pincode/area into the Google Places input
        places_input = page.locator("input.pac-target-input").first
        try:
            if not (await places_input.count() and await places_input.is_visible()):
                for fallback in (
                    'input[placeholder*="area" i]',
                    'input[placeholder*="location" i]',
                    'input[placeholder*="pincode" i]',
                    'input[placeholder*="search" i]',
                ):
                    places_input = page.locator(fallback).first
                    if await places_input.count() and await places_input.is_visible():
                        break

            await places_input.fill("")
            await places_input.type(pincode, delay=80)
            await page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"Location solve: could not type into places input: {e}")
            return

        # Step 3: Click the first Google Places suggestion
        suggestion_clicked = False
        for suggestion_sel in (".pac-item", '[class*="pac-item"]', '[class*="suggestion" i]'):
            try:
                suggestion = page.locator(suggestion_sel).first
                if await suggestion.count() and await suggestion.is_visible():
                    await suggestion.click(timeout=5000)
                    suggestion_clicked = True
                    break
            except Exception:
                continue

        if not suggestion_clicked:
            logger.warning("Location solve: no autocomplete suggestion appeared")
            return

        await page.wait_for_timeout(1500)

        # Step 4: Click "Confirm Location"
        confirm_clicked = False
        for confirm_sel in (
            'button.AddressFullscreenModal__confirm-button',
            'button:has-text("Confirm Location")',
            'button:has-text("Confirm")',
            '[class*="confirm" i] button',
        ):
            try:
                confirm = page.locator(confirm_sel).first
                if await confirm.count() and await confirm.is_visible():
                    await confirm.click(timeout=5000)
                    confirm_clicked = True
                    break
            except Exception:
                continue

        if not confirm_clicked:
            logger.warning("Location solve: could not click confirm button")
            return

        await page.wait_for_timeout(4000)
        logger.info(f"Location solve: completed fill for pincode {pincode}")

        # Persist cookies
        cookies_file = self.config.location.cookies_file
        if cookies_file:
            try:
                cookies = await page.context.cookies()
                loc_cookies = [
                    c for c in cookies
                    if any(k in c.get("name", "") for k in (
                        "location", "geolocation", "pincode", "address",
                        "polygon", "city", "lat", "lng",
                    ))
                ]
                if not loc_cookies:
                    logger.warning("Location solve: no location-matching cookies found, skipping save")
                else:
                    Path(cookies_file).parent.mkdir(parents=True, exist_ok=True)
                    with open(cookies_file, "w") as f:
                        json.dump(loc_cookies, f, indent=2)
                    logger.info(f"Location cookies saved to {cookies_file} ({len(loc_cookies)} cookies)")
            except Exception as e:
                logger.warning(f"Location solve: cookie save failed: {e}")

    async def _verify_location_pdp(self, page):
        """Verify location is established by loading a known PDP."""
        verify_url = getattr(self.config.location, "verify_url", None)
        if not verify_url:
            self.journey.location_verified = True
            logger.info("Location solve: no verify_url configured, marking verified")
            return

        for attempt in range(2):
            try:
                resp = await page.goto(verify_url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                # Dismiss any overlay that appeared after navigation
                try:
                    await page.keyboard.press("Escape")
                    await page.wait_for_timeout(500)
                except Exception:
                    pass
                has_product = await page.evaluate("""() => {
                    const atc = document.querySelector('[class*="add-to-cart" i], [class*="addtocart" i], button[id*="add-to-cart" i]');
                    const schema = document.querySelector('script[type="application/ld+json"]');
                    const price = document.querySelector('[class*="price" i], [class*="selling" i]');
                    const productName = document.querySelector('[class*="product-name" i], [class*="product-title" i], h1');
                    return !!(atc || schema || price || productName);
                }""")
                if has_product and resp and resp.status < 400:
                    self.journey.location_verified = True
                    logger.info(f"Location verified via PDP: {verify_url}")
                    return
                elif attempt == 0:
                    logger.debug(f"Location verify attempt 1 failed, retrying...")
                    await page.wait_for_timeout(3000)
                else:
                    self.journey.location_verified = False
                    logger.warning(f"Location verification failed: product signal not found at {verify_url}")
            except Exception as e:
                if attempt == 1:
                    self.journey.location_verified = False
                    logger.warning(f"Location verification failed: {e}")
                else:
                    logger.debug(f"Location verify attempt 1 error: {e}")
                    await page.wait_for_timeout(2000)

    # ----- helpers ----------------------------------------------------------

    async def _goto(self, page, url: str) -> bool:
        """Navigate to url. Retries once with a cooldown if a block/error page
        is detected (HTTP 200 with error UI — common on anti-automation SPAs).
        Stores the response status for per-step response_metadata.
        """
        for attempt in range(2):
            try:
                response = await page.goto(
                    url, timeout=45000, wait_until="domcontentloaded"
                )
                await page.wait_for_timeout(2500)
                self._last_response_status = (
                    response.status if response is not None else None
                )
                if response is not None and response.status >= 400:
                    logger.info(f"goto {url[:80]} → HTTP {response.status}")
                    return False

                # Detect block/soft-error pages (return HTTP 200 with error UI)
                if await self._is_blocked(page):
                    if attempt == 0:
                        logger.info(f"goto {url[:80]} → block/error page, retrying after cooldown")
                        await page.wait_for_timeout(8000)
                        continue
                    logger.warning(f"goto {url[:80]} → still blocked after retry")
                    return False

                # p007 parity: force declared viewport after navigation so
                # screenshots match the declared dimensions (same as crawlee_adapter)
                try:
                    await page.set_viewport_size({
                        "width": self.viewport["width"],
                        "height": self.viewport["height"],
                    })
                except Exception as e:
                    logger.debug(f"set_viewport_size failed: {e}")
                return True
            except Exception as e:
                logger.info(f"goto failed {url[:80]}: {e}")
                self._last_response_status = None
                return False
        return False

    async def _is_blocked(self, page) -> bool:
        """Return True if the current page is a block/challenge/soft-error page."""
        try:
            title = await page.title() or ""
            text_preview = await page.evaluate(
                "document.body?.innerText?.slice(0, 500) || ''"
            )
            return AntiCrawlerDetector.is_block_page(title, text_preview)
        except Exception:
            return False

    async def _find_add_to_cart(self, page):
        """Find a visible, enabled add-to-cart control.

        If none is visible on the first pass, try selecting a variant (color
        swatch) and re-check — some sites hide the button until a variant is
        chosen.
        """
        found = await self._scan_add_to_cart(page)
        if found:
            return found

        # Try selecting the first visible variant swatch, then re-scan
        for selector in VARIANT_SELECTORS:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=5000)
                    await page.wait_for_timeout(2000)
                    found = await self._scan_add_to_cart(page)
                    if found:
                        return found
            except Exception:
                continue
        return None

    async def _scan_add_to_cart(self, page):
        for selector in ADD_TO_CART_SELECTORS:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    text = (await loc.inner_text()).strip().lower()
                    if "sold out" in text or "out of stock" in text:
                        continue
                    return loc
            except Exception:
                continue
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=ADD_TO_CART_TEXT).first
                if await loc.count() and await loc.is_visible():
                    return loc
            except Exception:
                continue
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
        """Dismiss cookie consent, newsletter modals, and app-install banners.

        Location/pincode panels are NOT dismissed here — they are session
        preconditions handled via cookie injection before navigation.
        Elements matching location.panel_selectors are excluded from dismissal.
        """
        location_panel_sels = []
        if self.config and hasattr(self.config, "location"):
            location_panel_sels = getattr(self.config.location, "panel_selectors", [])

        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
        except Exception:
            pass

        close_selectors = [
            '[class*="cookie" i] [class*="close" i]',
            '[class*="cookie" i] [class*="accept" i]',
            '[class*="consent" i] [class*="close" i]',
            '[class*="consent" i] [class*="accept" i]',
            '[class*="newsletter" i] [class*="close" i]',
            '[class*="app-install" i] [class*="close" i]',
            '[class*="app-banner" i] [class*="close" i]',
            '[aria-label*="close" i]',
            'button[class*="close" i]',
            '[class*="modal" i] [class*="close" i]',
            '[class*="popup" i] [class*="close" i]',
        ]
        for selector in close_selectors:
            loc = page.locator(selector).first
            try:
                if await loc.count() and await loc.is_visible():
                    if location_panel_sels and await self._is_inside_location_panel(
                        page, loc, location_panel_sels
                    ):
                        logger.debug(f"Skipping dismiss: element matches location panel")
                        continue
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(400)
            except Exception:
                continue

        try:
            exclude_js = "false"
            if location_panel_sels:
                sel_list = ", ".join(location_panel_sels)
                exclude_js = f"el.closest('{sel_list}') !== null"

            removed = await page.evaluate(f"""() => {{
                let n = 0;
                const vw = window.innerWidth, vh = window.innerHeight;
                for (const el of document.querySelectorAll(
                    '[class*="modal" i], [class*="overlay" i], ' +
                    '[class*="backdrop" i], [class*="popup" i]'
                )) {{
                    if ({exclude_js}) continue;
                    const s = getComputedStyle(el);
                    if (s.position !== 'fixed' && s.position !== 'absolute') continue;
                    if (s.display === 'none' || s.visibility === 'hidden') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width >= vw * 0.8 && r.height >= vh * 0.8) {{
                        el.style.display = 'none';
                        n++;
                    }}
                }}
                return n;
            }}""")
            if removed:
                logger.debug(f"Removed {removed} blocking overlay(s) via JS fallback")
        except Exception:
            pass

    async def _is_inside_location_panel(self, page, locator, panel_selectors):
        """Check if a locator element is inside a location panel."""
        try:
            for panel_sel in panel_selectors:
                is_inside = await locator.evaluate(
                    f"el => el.closest('{panel_sel}') !== null"
                )
                if is_inside:
                    return True
        except Exception:
            pass
        return False

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

        if page_type:
            page_data["page_type"] = page_type
            page_data["classifier_confidence"] = 1.0
        else:
            classified_type, confidence = PageClassifier.classify_page(url, page_data)
            page_data["page_type"] = classified_type
            page_data["classifier_confidence"] = confidence
        page_data["flow"] = flow
        if extra:
            page_data.update(extra)

        # Response metadata + page_state (S1.10 item 6) — record whether this
        # step landed on a block/error page so readiness can exclude it.
        try:
            text_preview = await page.evaluate(
                "document.body?.innerText?.slice(0, 500) || ''"
            )
        except Exception:
            text_preview = ""
        block_signals = AntiCrawlerDetector.block_signals(title, text_preview)
        page_data["response_metadata"] = {
            "status": self._last_response_status,
            "blocked": bool(block_signals),
            "block_signals": block_signals,
        }
        page_data["page_state"] = "blocked" if block_signals else "live"

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

        # Design data collection (S1.11)
        try:
            from ux_journey_scraper.core.design_data_collector import collect_and_merge_design_data
            await collect_and_merge_design_data(page, page_data)
        except Exception as e:
            logger.warning(f"flow design data collection failed: {e}")

        self.journey.add_step(
            JourneyStep(
                step_number=self.step_num,
                url=url,
                title=title,
                screenshot_path=screenshot_path,
                page_data=page_data,
            )
        )
