"""
Cookie persistence across visit sessions.

Persists browser cookies across multiple visit sessions to make the scraper
appear as a returning visitor, not a new user every time.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CookieJar:
    """
    Persists browser cookies across visit sessions.

    When persist_cookies = True, cookies from each session are saved
    and injected into the next session for the same domain.
    This makes the scraper appear as a returning visitor.
    """

    def __init__(self, persist_path: Optional[Path] = None):
        """
        Initialize cookie jar.

        Args:
            persist_path: Optional path to save cookies to disk for resume capability.
                         If None, cookies are only kept in memory.
        """
        self._cookies: Dict[str, List[dict]] = {}
        self._persist_path = persist_path  # Optional: save to disk for resume

    def update(self, domain: str, cookies: List[dict]):
        """
        Save cookies from a completed session.

        Args:
            domain: Domain these cookies belong to (e.g., "example.com")
            cookies: List of cookie dicts from Playwright's context.cookies()
        """
        self._cookies[domain] = cookies
        logger.debug(f"CookieJar updated for {domain}: {len(cookies)} cookies")

        if self._persist_path:
            self._save_to_disk()

    def get(self, domain: str) -> List[dict]:
        """
        Get cookies for injection into a new session.

        Args:
            domain: Domain to get cookies for

        Returns:
            List of cookie dicts ready for context.add_cookies()
        """
        return self._cookies.get(domain, [])

    def has_cookies(self, domain: str) -> bool:
        """
        Check if we have any cookies saved for this domain.

        Args:
            domain: Domain to check

        Returns:
            True if cookies exist for this domain
        """
        return domain in self._cookies and len(self._cookies[domain]) > 0

    def clear(self, domain: Optional[str] = None):
        """
        Clear cookies for a domain or all domains.

        Args:
            domain: Domain to clear. If None, clears all cookies.
        """
        if domain:
            if domain in self._cookies:
                del self._cookies[domain]
                logger.debug(f"Cleared cookies for {domain}")
        else:
            self._cookies.clear()
            logger.debug("Cleared all cookies")

        if self._persist_path:
            self._save_to_disk()

    def get_all_domains(self) -> List[str]:
        """
        Get list of all domains with saved cookies.

        Returns:
            List of domain names
        """
        return list(self._cookies.keys())

    def _save_to_disk(self):
        """Persist to disk for resume capability."""
        if not self._persist_path:
            return

        try:
            data = {
                "saved_at": datetime.utcnow().isoformat(),
                "domains": {
                    domain: cookies for domain, cookies in self._cookies.items()
                },
            }

            # Ensure parent directory exists
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)

            tmp_path = self._persist_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
            tmp_path.replace(self._persist_path)  # Atomic on POSIX
            logger.debug(f"Saved cookies to {self._persist_path}")

        except Exception as e:
            logger.warning(f"CookieJar disk save failed: {e}")

    def load_from_disk(self):
        """Restore from disk (for resume)."""
        if not self._persist_path or not self._persist_path.exists():
            return

        try:
            data = json.loads(self._persist_path.read_text())
            self._cookies = data.get("domains", {})
            saved_at = data.get("saved_at", "unknown")
            logger.info(
                f"CookieJar restored: {len(self._cookies)} domains from {saved_at}"
            )

        except Exception as e:
            logger.warning(f"CookieJar disk load failed: {e}")

    def export_cookies(self, domain: str) -> str:
        """
        Export cookies for a domain as JSON string.

        Args:
            domain: Domain to export

        Returns:
            JSON string of cookies
        """
        cookies = self.get(domain)
        return json.dumps(cookies, indent=2)

    def import_cookies(self, domain: str, cookies_json: str):
        """
        Import cookies from a JSON string.

        Args:
            domain: Domain to import for
            cookies_json: JSON string of cookies
        """
        try:
            cookies = json.loads(cookies_json)
            self.update(domain, cookies)
            logger.info(f"Imported {len(cookies)} cookies for {domain}")

        except json.JSONDecodeError as e:
            logger.error(f"Failed to import cookies: {e}")
            raise ValueError(f"Invalid cookies JSON: {e}")
