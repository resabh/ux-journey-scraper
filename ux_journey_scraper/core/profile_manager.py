"""Persistent browser profile for detection avoidance.

Maintains a long-lived cookie profile at ~/.ux-journey-scraper/profile.json
that accumulates cookies from warm-up sites (Google, YouTube, Amazon, Wikipedia)
and target site scrapes. This makes the scraper appear as a returning user
with browsing history, not a fresh bot.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ux_journey_scraper.core.cookie_jar import CookieJar

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_PATH = Path.home() / ".ux-journey-scraper" / "profile.json"


class ProfileManager:
    """Manages a persistent browser profile with cookie history."""

    WARMUP_SITES = {
        "google.com": "https://www.google.com",
        "youtube.com": "https://www.youtube.com",
        "amazon.com": "https://www.amazon.com",
        "wikipedia.org": "https://www.wikipedia.org",
    }

    def __init__(self, profile_path: Optional[Path] = None, max_age_days: int = 7):
        self._profile_path = profile_path or DEFAULT_PROFILE_PATH
        self._max_age_days = max_age_days
        self._domains: dict[str, List[dict]] = {}
        self._saved_at: Optional[str] = None
        self._load()

    def _load(self):
        """Load profile from disk if it exists."""
        if not self._profile_path.exists():
            return
        try:
            data = json.loads(self._profile_path.read_text())
            self._domains = data.get("domains", {})
            self._saved_at = data.get("saved_at")
            logger.debug(f"Profile loaded: {len(self._domains)} domains from {self._saved_at}")
        except Exception as e:
            logger.warning(f"Profile load failed: {e}")

    def _save(self):
        """Persist profile to disk."""
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        self._saved_at = datetime.utcnow().isoformat()
        data = {"saved_at": self._saved_at, "domains": self._domains}
        self._profile_path.write_text(json.dumps(data, indent=2, default=str))
        logger.debug(f"Profile saved: {len(self._domains)} domains")

    def is_fresh(self) -> bool:
        """True if profile doesn't exist or is older than max_age_days."""
        if not self._saved_at or not self._domains:
            return True
        try:
            saved = datetime.fromisoformat(self._saved_at)
            age = datetime.utcnow() - saved
            return age > timedelta(days=self._max_age_days)
        except (ValueError, TypeError):
            return True

    def get_cookies(self) -> List[dict]:
        """All cookies from all domains in the profile."""
        all_cookies = []
        for cookies in self._domains.values():
            all_cookies.extend(cookies)
        return all_cookies

    def get_cookies_for_domain(self, domain: str) -> List[dict]:
        """Cookies for a specific domain."""
        return self._domains.get(domain, [])

    def merge_cookies(self, domain: str, cookies: List[dict]) -> None:
        """Merge cookies for a domain into the profile. Preserves other domains."""
        self._domains[domain] = cookies
        self._save()
        logger.debug(f"Profile merged: {len(cookies)} cookies for {domain}")

    def seed_cookie_jar(self, cookie_jar: CookieJar, target_domain: str) -> None:
        """Seed a CookieJar with warm-up cookies and target domain cookies."""
        seeded = 0
        for domain, cookies in self._domains.items():
            if cookies:
                cookie_jar.update(domain, cookies)
                seeded += len(cookies)
        logger.info(f"Seeded CookieJar: {seeded} cookies from {len(self._domains)} domains")

    def absorb_cookie_jar(self, cookie_jar: CookieJar) -> None:
        """Merge all cookies from a completed crawl back into the profile."""
        absorbed = 0
        for domain in cookie_jar.get_all_domains():
            cookies = cookie_jar.get(domain)
            if cookies:
                self._domains[domain] = cookies
                absorbed += len(cookies)
        self._save()
        logger.info(f"Absorbed {absorbed} cookies from crawl into profile")

    async def warm_up(self, browser_type: str = "webkit") -> None:
        """Visit mainstream sites to accumulate real cookies."""
        from playwright.async_api import async_playwright
        from ux_journey_scraper.core.human_behaviour import HumanBehaviour

        logger.info("Starting profile warm-up...")
        async with async_playwright() as p:
            browser = await p.webkit.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
            )
            page = await context.new_page()

            for domain, url in self.WARMUP_SITES.items():
                try:
                    logger.info(f"  Warming up: {url}")
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    await HumanBehaviour.human_scroll(page, direction="down", distance=500)
                    await HumanBehaviour.human_delay(5000, 10000, reason="page_load")
                except Exception as e:
                    logger.warning(f"  Warm-up failed for {domain}: {e}")

            cookies = await context.cookies()
            domain_cookies: dict[str, list] = {}
            for cookie in cookies:
                d = cookie.get("domain", "").lstrip(".")
                if d not in domain_cookies:
                    domain_cookies[d] = []
                domain_cookies[d].append(cookie)

            for domain, cooks in domain_cookies.items():
                self._domains[domain] = cooks

            self._save()
            logger.info(f"Warm-up complete: {len(cookies)} cookies from {len(domain_cookies)} domains")
            await browser.close()
