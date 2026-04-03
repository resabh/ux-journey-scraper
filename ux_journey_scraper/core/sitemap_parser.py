"""
Sitemap parser for discovering all pages on a website.

Parses sitemap.xml, sitemap_index.xml, and robots.txt to find all URLs.
Feeds discovered URLs into the crawl queue for complete site coverage.
"""

import logging
import re
from typing import List, Set
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import aiohttp

logger = logging.getLogger(__name__)

# Sitemap XML namespace
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapParser:
    """Discover all URLs from a website's sitemap."""

    def __init__(self, base_url: str, max_urls: int = 5000):
        self.base_url = base_url.rstrip("/")
        self.base_domain = urlparse(base_url).netloc.replace("www.", "")
        self.max_urls = max_urls
        self.discovered_urls: Set[str] = set()

    async def discover_all(self) -> List[str]:
        """Discover all URLs from sitemap + robots.txt.

        Strategy:
        1. Check robots.txt for sitemap locations
        2. Try common sitemap paths
        3. Parse all sitemaps (including sitemap indexes)

        Returns:
            List of discovered URLs, sorted by path depth (shallow first).
        """
        sitemap_urls = await self._find_sitemap_urls()

        if not sitemap_urls:
            logger.info("No sitemaps found, will rely on link discovery")
            return []

        for sitemap_url in sitemap_urls:
            if len(self.discovered_urls) >= self.max_urls:
                break
            await self._parse_sitemap(sitemap_url)

        # Sort by path depth (homepage first, deep pages last)
        sorted_urls = sorted(
            self.discovered_urls,
            key=lambda u: u.count("/"),
        )

        logger.info(f"Sitemap discovery: {len(sorted_urls)} URLs found")
        return sorted_urls[: self.max_urls]

    async def _find_sitemap_urls(self) -> List[str]:
        """Find sitemap URLs from robots.txt and common paths."""
        sitemap_urls = []

        # 1. Check robots.txt
        try:
            robots_url = f"{self.base_url}/robots.txt"
            async with aiohttp.ClientSession() as session:
                async with session.get(robots_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        for line in text.splitlines():
                            if line.lower().startswith("sitemap:"):
                                url = line.split(":", 1)[1].strip()
                                sitemap_urls.append(url)
                                logger.debug(f"Found sitemap in robots.txt: {url}")
        except Exception as e:
            logger.debug(f"robots.txt check failed: {e}")

        # 2. Try common paths if none found
        if not sitemap_urls:
            common_paths = [
                "/sitemap.xml",
                "/sitemap_index.xml",
                "/sitemap/sitemap.xml",
                "/sitemaps/sitemap.xml",
            ]
            for path in common_paths:
                url = f"{self.base_url}{path}"
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True
                        ) as resp:
                            if resp.status == 200:
                                content_type = resp.headers.get("content-type", "")
                                if "xml" in content_type or "text" in content_type:
                                    sitemap_urls.append(url)
                                    logger.debug(f"Found sitemap at: {url}")
                                    break
                except Exception:
                    continue

        return sitemap_urls

    async def _parse_sitemap(self, sitemap_url: str) -> None:
        """Parse a sitemap XML and extract URLs.

        Handles both regular sitemaps and sitemap indexes.
        """
        if len(self.discovered_urls) >= self.max_urls:
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    sitemap_url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"Sitemap fetch failed ({resp.status}): {sitemap_url}")
                        return
                    content = await resp.text()
        except Exception as e:
            logger.debug(f"Sitemap fetch error: {e}")
            return

        try:
            # Strip DOCTYPE to prevent XXE (external entity injection).
            # Sitemaps should never have DTDs — only malicious ones do.
            if "<!DOCTYPE" in content or "<!ENTITY" in content:
                logger.warning(f"Sitemap contains DOCTYPE/ENTITY — stripping for XXE safety")
                import re as _re
                content = _re.sub(r"<!DOCTYPE[^>]*>", "", content, flags=_re.IGNORECASE)
                content = _re.sub(r"<!ENTITY[^>]*>", "", content, flags=_re.IGNORECASE)
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as e:
            logger.debug(f"Sitemap XML parse error: {e}")
            return

        # Check if this is a sitemap index
        tag = root.tag.lower()
        if "sitemapindex" in tag:
            # Parse each child sitemap
            for sitemap in root.findall("sm:sitemap", SITEMAP_NS):
                loc = sitemap.find("sm:loc", SITEMAP_NS)
                if loc is not None and loc.text:
                    await self._parse_sitemap(loc.text.strip())
            # Also try without namespace
            for sitemap in root.findall("sitemap"):
                loc = sitemap.find("loc")
                if loc is not None and loc.text:
                    await self._parse_sitemap(loc.text.strip())
        else:
            # Regular sitemap — extract URLs
            for url_elem in root.findall("sm:url", SITEMAP_NS):
                loc = url_elem.find("sm:loc", SITEMAP_NS)
                if loc is not None and loc.text:
                    self._add_url(loc.text.strip())
            # Also try without namespace
            for url_elem in root.findall("url"):
                loc = url_elem.find("loc")
                if loc is not None and loc.text:
                    self._add_url(loc.text.strip())

    def _add_url(self, url: str) -> None:
        """Add a URL if it's internal and not already discovered."""
        if len(self.discovered_urls) >= self.max_urls:
            return

        parsed = urlparse(url)

        # Must be same domain
        if self.base_domain not in parsed.netloc:
            return

        # Skip non-page resources
        skip_extensions = {
            ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
            ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
            ".pdf", ".zip", ".gz", ".mp4", ".mp3",
        }
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in skip_extensions):
            return

        self.discovered_urls.add(url)
