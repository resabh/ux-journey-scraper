"""Anti-crawler detection for e-commerce traps and blocks.

Detects honeypot links, infinite pagination, empty pages, and block pages
that sites use to trap or block automated crawlers.
"""

import re
from typing import Dict, List
from urllib.parse import urlparse, parse_qs


# Signatures that indicate a block/challenge page
BLOCK_SIGNATURES = [
    "access denied",
    "you don't have permission",
    "forbidden",
    "please verify you are human",
    "verify you are not a robot",
    "captcha",
    "checking your browser",
    "just a moment",
    "enable javascript and cookies",
    "blocked",
    "rate limit",
    "too many requests",
    "bot detected",
    "unusual traffic",
    "automated access",
    "security check",
    "challenge-platform",
    "cf-browser-verification",
    "please wait while we verify",
    "pardon our interruption",
]

# Regex patterns for pagination URL segments
PAGINATION_PATTERNS = [
    re.compile(r"[?&]page=\d+", re.IGNORECASE),
    re.compile(r"/page/\d+", re.IGNORECASE),
    re.compile(r"[?&]offset=\d+", re.IGNORECASE),
    re.compile(r"[?&]start=\d+", re.IGNORECASE),
    re.compile(r"[?&]p=\d+", re.IGNORECASE),
]

# Regex to strip pagination params/segments for grouping
PAGINATION_STRIP = [
    (re.compile(r"[?&]page=\d+", re.IGNORECASE), ""),
    (re.compile(r"/page/\d+", re.IGNORECASE), ""),
    (re.compile(r"[?&]offset=\d+", re.IGNORECASE), ""),
    (re.compile(r"[?&]start=\d+", re.IGNORECASE), ""),
    (re.compile(r"[?&]p=\d+", re.IGNORECASE), ""),
]


class AntiCrawlerDetector:
    """Detects and avoids common anti-crawler traps on e-commerce sites."""

    @staticmethod
    def is_honeypot_link(styles: Dict[str, str]) -> bool:
        """Check if a link is a honeypot based on its computed styles.

        Honeypot links are invisible to users but visible to crawlers.
        They use CSS to hide elements (display:none, visibility:hidden, opacity:0).

        Args:
            styles: Dict with CSS properties like display, visibility, opacity.

        Returns:
            True if the link appears to be a honeypot.
        """
        if styles.get("display", "").lower() == "none":
            return True
        if styles.get("visibility", "").lower() == "hidden":
            return True
        try:
            if float(styles.get("opacity", "1")) == 0:
                return True
        except (ValueError, TypeError):
            pass
        return False

    @staticmethod
    def is_pagination_url(url: str) -> bool:
        """Check if a URL is a pagination URL.

        Args:
            url: The URL to check.

        Returns:
            True if the URL matches common pagination patterns.
        """
        return any(pattern.search(url) for pattern in PAGINATION_PATTERNS)

    @staticmethod
    def limit_pagination(urls: List[str], max_per_pattern: int = 3) -> List[str]:
        """Limit pagination URLs to avoid infinite crawling.

        Groups URLs by their base path (with pagination params stripped)
        and limits each group to max_per_pattern URLs.

        Args:
            urls: List of URLs to filter.
            max_per_pattern: Maximum URLs to keep per pagination group.

        Returns:
            Filtered list of URLs.
        """
        groups: Dict[str, List[str]] = {}

        for url in urls:
            base = url
            for pattern, replacement in PAGINATION_STRIP:
                base = pattern.sub(replacement, base)
            # Clean up leftover ? or & at end
            base = re.sub(r"[?&]$", "", base)

            if base not in groups:
                groups[base] = []
            groups[base].append(url)

        result = []
        for group_urls in groups.values():
            result.extend(group_urls[:max_per_pattern])

        return result

    @staticmethod
    def is_empty_page(html: str, text_content: str) -> bool:
        """Check if a page is effectively empty.

        A page is empty only if it has almost no text AND almost no HTML
        structure. This avoids false positives on image-heavy pages
        (fashion, hero sections) that have minimal text but rich content.

        Args:
            html: Raw HTML of the page.
            text_content: Extracted visible text content.

        Returns:
            True if the page appears to be empty/placeholder.
        """
        text_len = len(text_content.strip())
        html_len = len(html.strip())

        # Very short text AND very short HTML = truly empty
        if text_len < 50 and html_len < 500:
            return True

        # Has meaningful HTML structure (images, divs, etc.) = not empty
        if html_len > 2000:
            return False

        # Moderate HTML but almost no text = suspicious but allow
        return text_len < 20

    @staticmethod
    def is_block_page(title: str, text_preview: str) -> bool:
        """Check if a page is a block/challenge page.

        Args:
            title: Page title.
            text_preview: First portion of visible text on the page.

        Returns:
            True if the page appears to be blocking the crawler.
        """
        combined = f"{title} {text_preview}".lower()
        return any(sig in combined for sig in BLOCK_SIGNATURES)
