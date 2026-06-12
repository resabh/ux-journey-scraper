"""Persistent browser profile for detection avoidance.

Maintains a long-lived cookie profile at ~/.ux-journey-scraper/profile.json
that accumulates cookies from warm-up sites and target site scrapes. This
makes the scraper appear as a returning user with browsing history, not a
fresh bot.
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ux_journey_scraper.core.cookie_jar import CookieJar

logger = logging.getLogger(__name__)

DEFAULT_PROFILE_PATH = Path.home() / ".ux-journey-scraper" / "profile.json"


class ProfileManager:
    """Manages a persistent browser profile with cookie history."""

    # Warm-up sites grouped by category for realistic browsing persona
    WARMUP_SITES = [
        # Search engines (always first — that's how real sessions start)
        {"url": "https://www.google.com", "search": "best products online shopping", "dwell": (3, 6)},
        {"url": "https://www.google.com/search?q=latest+fashion+trends+2026", "dwell": (5, 10)},
        # Video/social (high cookie yield)
        {"url": "https://www.youtube.com", "dwell": (8, 15)},
        {"url": "https://www.reddit.com", "dwell": (5, 10)},
        # E-commerce (builds shopping persona)
        {"url": "https://www.amazon.com", "dwell": (8, 15)},
        {"url": "https://www.amazon.com/s?k=wireless+headphones", "dwell": (5, 10)},
        {"url": "https://www.flipkart.com", "dwell": (5, 10)},
        # News/reference (broadens cookie diversity)
        {"url": "https://www.wikipedia.org", "dwell": (3, 6)},
        {"url": "https://www.bbc.com", "dwell": (3, 6)},
        {"url": "https://www.weather.com", "dwell": (2, 5)},
        # Tech/tools (shows diverse browsing)
        {"url": "https://www.github.com", "dwell": (3, 6)},
        {"url": "https://www.stackoverflow.com", "dwell": (3, 6)},
    ]

    def __init__(self, profile_path: Optional[Path] = None, max_age_days: int = 7):
        self._profile_path = profile_path or DEFAULT_PROFILE_PATH
        self._max_age_days = max_age_days
        self._domains: Dict[str, List[dict]] = {}
        self._local_storage: Dict[str, Dict[str, str]] = {}
        self._saved_at: Optional[str] = None
        self._load()

    def _load(self):
        """Load profile from disk if it exists. Prunes expired cookies."""
        if not self._profile_path.exists():
            return
        try:
            data = json.loads(self._profile_path.read_text())
            self._domains = data.get("domains", {})
            self._local_storage = data.get("local_storage", {})
            self._saved_at = data.get("saved_at")
            pruned = self._prune_expired()
            logger.debug(
                f"Profile loaded: {len(self._domains)} domains from {self._saved_at}"
                + (f" ({pruned} expired cookies removed)" if pruned else "")
            )
        except Exception as e:
            logger.warning(f"Profile load failed: {e}")

    def _prune_expired(self) -> int:
        """Remove expired cookies from all domains. Returns count removed."""
        now = time.time()
        total_removed = 0
        for domain in list(self._domains.keys()):
            cookies = self._domains[domain]
            valid = []
            for c in cookies:
                # Playwright cookies use "expires" as Unix timestamp (-1 = session)
                expires = c.get("expires", -1)
                if expires == -1 or expires == 0:
                    valid.append(c)  # Session cookie or no expiry — keep
                elif expires > now:
                    valid.append(c)  # Not expired — keep
                else:
                    total_removed += 1
            if valid:
                self._domains[domain] = valid
            else:
                del self._domains[domain]
        return total_removed

    def _save(self):
        """Persist profile to disk with restricted permissions (atomic write)."""
        self._profile_path.parent.mkdir(parents=True, exist_ok=True)
        self._saved_at = datetime.utcnow().isoformat()
        data = {
            "saved_at": self._saved_at,
            "domains": self._domains,
            "local_storage": self._local_storage,
        }
        # Atomic write: set permissions on tmp file BEFORE rename
        # to avoid window where file is world-readable with session tokens
        tmp_path = self._profile_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, default=str))
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(self._profile_path)  # Atomic on POSIX
        logger.debug(f"Profile saved: {len(self._domains)} domains")

    def get_health(self) -> Dict:
        """Profile health diagnostics.

        Returns:
            Dict with total_cookies, domains, expired_count, local_storage_domains,
            age_hours, is_fresh, saved_at.
        """
        now = time.time()
        total = 0
        expired = 0
        for cookies in self._domains.values():
            for c in cookies:
                total += 1
                exp = c.get("expires", -1)
                if exp not in (-1, 0) and exp <= now:
                    expired += 1

        age_hours = None
        if self._saved_at:
            try:
                saved = datetime.fromisoformat(self._saved_at)
                age_hours = round((datetime.utcnow() - saved).total_seconds() / 3600, 1)
            except (ValueError, TypeError):
                pass

        return {
            "total_cookies": total,
            "expired_cookies": expired,
            "valid_cookies": total - expired,
            "domains": len(self._domains),
            "domain_list": sorted(self._domains.keys()),
            "local_storage_domains": len(self._local_storage),
            "age_hours": age_hours,
            "is_fresh": self.is_fresh(),
            "saved_at": self._saved_at,
        }

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

    @staticmethod
    def _cookie_key(cookie: dict) -> Tuple[str, str, str]:
        """Unique key for a cookie: (domain, name, path)."""
        return (
            cookie.get("domain", ""),
            cookie.get("name", ""),
            cookie.get("path", "/"),
        )

    def merge_cookies(self, domain: str, cookies: List[dict]) -> None:
        """Merge cookies for a domain by upserting on (domain, name, path).

        Preserves existing cookies not present in the new batch (e.g. cookies
        set by other pages on the same domain).
        """
        existing = {self._cookie_key(c): c for c in self._domains.get(domain, [])}
        for cookie in cookies:
            existing[self._cookie_key(cookie)] = cookie
        self._domains[domain] = list(existing.values())
        self._save()
        logger.debug(f"Profile merged: {len(cookies)} cookies for {domain} (total: {len(self._domains[domain])})")

    def seed_cookie_jar(self, cookie_jar: CookieJar, target_domain: str) -> None:
        """Seed a CookieJar with target domain cookies and localStorage.

        Only injects cookies that match the target domain to prevent
        cross-site cookie leakage (e.g. Google cookies into target site).
        Also seeds localStorage entries for the target domain.
        """
        target_base = target_domain.replace("www.", "")
        seeded = 0
        for domain, cookies in self._domains.items():
            if cookies and target_base in domain:
                cookie_jar.update(domain, cookies)
                seeded += len(cookies)

        # Seed localStorage for target domain
        ls_seeded = 0
        for domain, entries in self._local_storage.items():
            if target_base in domain and entries:
                cookie_jar.set_local_storage(domain, entries)
                ls_seeded += len(entries)

        logger.info(f"Seeded CookieJar: {seeded} cookies, {ls_seeded} localStorage entries for {target_base}")

    def merge_local_storage(self, domain: str, entries: Dict[str, str]) -> None:
        """Merge localStorage entries for a domain into the profile."""
        existing = self._local_storage.get(domain, {})
        existing.update(entries)
        self._local_storage[domain] = existing
        self._save()
        logger.debug(f"Profile localStorage merged: {len(entries)} entries for {domain}")

    def get_local_storage(self, domain: str) -> Dict[str, str]:
        """Get localStorage entries for a domain."""
        return self._local_storage.get(domain, {})

    def absorb_cookie_jar(self, cookie_jar: CookieJar) -> None:
        """Merge all cookies and localStorage from a completed crawl back into the profile."""
        absorbed = 0
        for domain in cookie_jar.get_all_domains():
            cookies = cookie_jar.get(domain)
            if cookies:
                self._domains[domain] = cookies
                absorbed += len(cookies)
            ls = cookie_jar.get_local_storage(domain)
            if ls:
                existing = self._local_storage.get(domain, {})
                existing.update(ls)
                self._local_storage[domain] = existing
        self._save()
        logger.info(f"Absorbed {absorbed} cookies from crawl into profile")

    def import_cookies_file(self, filepath: str, format: str = "auto") -> int:
        """Import cookies from a browser export file into the profile.

        Supports Netscape/wget format (exported by browser extensions like
        "EditThisCookie", "Cookie-Editor", or `cookies.txt` exporters) and
        JSON format (Playwright-style or browser extension JSON exports).

        Args:
            filepath: Path to the cookies file.
            format: "netscape", "json", or "auto" (detect from extension/content).

        Returns:
            Number of cookies imported.
        """
        path = Path(filepath)
        content = path.read_text(encoding="utf-8")

        if format == "auto":
            if path.suffix == ".json":
                format = "json"
            elif content.lstrip().startswith("[") or content.lstrip().startswith("{"):
                format = "json"
            else:
                format = "netscape"

        if format == "json":
            return self._import_json_cookies(content)
        else:
            return self._import_netscape_cookies(content)

    def _import_json_cookies(self, content: str) -> int:
        """Import cookies from JSON format (Playwright or browser extension)."""
        data = json.loads(content)
        if isinstance(data, dict):
            # Could be {"domains": {...}} format (our own profile export)
            if "domains" in data:
                for domain, cookies in data["domains"].items():
                    for cookie in cookies:
                        self.merge_cookies(domain, [cookie])
                return sum(len(v) for v in data["domains"].values())
            # Single cookie dict
            data = [data]

        # List of cookie dicts
        imported = 0
        for cookie in data:
            domain = cookie.get("domain", "").lstrip(".").removeprefix("www.")
            if not domain:
                continue
            # Normalize to Playwright cookie format
            normalized = {
                "name": cookie.get("name", ""),
                "value": cookie.get("value", ""),
                "domain": cookie.get("domain", ""),
                "path": cookie.get("path", "/"),
            }
            # Preserve optional fields if present
            for field in ("expires", "httpOnly", "secure", "sameSite"):
                if field in cookie:
                    normalized[field] = cookie[field]
            # Handle expirationDate (browser extension format) → expires
            if "expirationDate" in cookie and "expires" not in normalized:
                normalized["expires"] = cookie["expirationDate"]

            self.merge_cookies(domain, [normalized])
            imported += 1

        logger.info(f"Imported {imported} cookies from JSON")
        return imported

    def _import_netscape_cookies(self, content: str) -> int:
        """Import cookies from Netscape/wget cookies.txt format.

        Format: domain  flag  path  secure  expiry  name  value
        Lines starting with # are comments.
        """
        imported = 0
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain_raw, _flag, path, secure, expiry, name, value = parts[:7]
            domain = domain_raw.lstrip(".").removeprefix("www.")
            cookie = {
                "name": name,
                "value": value,
                "domain": domain_raw,
                "path": path,
                "secure": secure.upper() == "TRUE",
            }
            try:
                exp = int(expiry)
                if exp > 0:
                    cookie["expires"] = exp
            except (ValueError, TypeError):
                pass

            self.merge_cookies(domain, [cookie])
            imported += 1

        logger.info(f"Imported {imported} cookies from Netscape format")
        return imported

    def export_profile(self, filepath: str) -> None:
        """Export the full profile to a JSON file (for sharing between machines)."""
        data = {
            "saved_at": self._saved_at,
            "domains": self._domains,
            "local_storage": self._local_storage,
        }
        Path(filepath).write_text(json.dumps(data, indent=2, default=str))
        logger.info(f"Profile exported to {filepath}: {len(self._domains)} domains")

    async def warm_up(self, browser_type: str = "webkit", user_agent: Optional[str] = None) -> None:
        """Visit mainstream sites to build a realistic browsing persona.

        Browses 12 sites across search, video, e-commerce, news, and tech
        categories. Performs searches, scrolls, and dwells on pages to
        accumulate real cookies and build browsing history that anti-bot
        systems expect from genuine users.

        Args:
            browser_type: Browser engine to use.
            user_agent: UA string to use. If None, uses a default Safari UA.
                        Pass the crawl platform's UA to avoid cookie/UA mismatch.
        """
        valid_browsers = ("webkit", "chromium", "firefox")
        if browser_type not in valid_browsers:
            raise ValueError(f"Invalid browser_type: {browser_type!r}. Must be one of {valid_browsers}")

        default_ua = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15"
        )

        from playwright.async_api import async_playwright
        from ux_journey_scraper.core.human_behaviour import HumanBehaviour

        logger.info("Starting profile warm-up...")

        # Full shuffle — hardcoded order is a bot signature
        sites = list(self.WARMUP_SITES)
        random.shuffle(sites)

        async with async_playwright() as p:
            # Use the requested browser engine
            browser_launcher = getattr(p, browser_type, p.webkit)
            browser = await browser_launcher.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent or default_ua,
            )
            page = await context.new_page()
            visited = 0

            for site in sites:
                url = site["url"]
                min_dwell, max_dwell = site["dwell"]
                try:
                    logger.info(f"  Warming up: {url[:60]}")
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)

                    # Scroll down naturally
                    scroll_distance = random.randint(300, 800)
                    await HumanBehaviour.human_scroll(page, direction="down", distance=scroll_distance)

                    # Dwell on page
                    dwell_ms = random.randint(min_dwell * 1000, max_dwell * 1000)
                    await HumanBehaviour.human_delay(dwell_ms, dwell_ms + 2000, reason="page_load")

                    # Sometimes scroll more (50% chance)
                    if random.random() > 0.5:
                        await HumanBehaviour.human_scroll(page, direction="down", distance=random.randint(200, 500))
                        await HumanBehaviour.human_delay(2000, 4000, reason="page_load")

                    # Harvest localStorage from this page
                    try:
                        ls_entries = await page.evaluate(
                            "() => { try { return Object.fromEntries(Object.entries(localStorage)); } catch(e) { return {}; } }"
                        )
                        if ls_entries:
                            from urllib.parse import urlparse as _urlparse
                            domain = _urlparse(url).netloc.replace("www.", "")
                            self._local_storage[domain] = ls_entries
                    except Exception:
                        pass  # localStorage blocked on some sites (cross-origin)

                    visited += 1
                except Exception as e:
                    logger.warning(f"  Warm-up failed for {url[:40]}: {e}")

            # Harvest all cookies
            cookies = await context.cookies()
            domain_cookies: Dict[str, list] = {}
            for cookie in cookies:
                d = cookie.get("domain", "").lstrip(".").removeprefix("www.")
                if d not in domain_cookies:
                    domain_cookies[d] = []
                domain_cookies[d].append(cookie)

            for domain, cooks in domain_cookies.items():
                self._domains[domain] = cooks

            self._save()
            logger.info(
                f"Warm-up complete: {len(cookies)} cookies from "
                f"{len(domain_cookies)} domains ({visited}/{len(sites)} sites visited)"
            )
            await browser.close()
