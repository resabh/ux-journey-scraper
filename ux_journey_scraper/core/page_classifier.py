"""Classify pages by type using URL patterns and content analysis."""
import re
from urllib.parse import urlparse, parse_qs


class PageClassifier:
    """Classify e-commerce page types from URLs and content."""

    URL_RULES = [
        ("homepage", [r"^/$", r"^$"]),
        ("cart", [r"/cart", r"/bag", r"/basket"]),
        ("checkout", [r"/checkout", r"/payment", r"/billing", r"/review.*order", r"/place.*order"]),
        ("search", [r"/search", r"/s\?", r"[?&]q=", r"/browse\?query"]),
        ("account", [r"/login", r"/signup", r"/sign-in", r"/register", r"/myaccount", r"/account",
                      r"/profile", r"/dashboard", r"/orders", r"/wishlist"]),
        ("policy", [r"/privacy", r"/terms", r"/policy", r"/shipping.*policy", r"/return.*policy",
                     r"/refund", r"/cookie.*policy", r"/legal", r"/disclaimer"]),
        ("info", [r"/about", r"/contact", r"/faq", r"/help", r"/support", r"/store-locator", r"/stores"]),
        ("content", [r"/blog", r"/article", r"/news", r"/magazine", r"/stories"]),
        ("pdp", [r"/p/", r"/product/", r"/dp/", r"/item/", r"/pd/",
                 r"/products/[^/]+/[^/]+$", r"\d+\.html$"]),
        ("plp", [r"/c/", r"/category/", r"/categories/", r"/shop/", r"/collection",
                 r"/products/[^/]+$", r"/products\?"]),
    ]

    @classmethod
    def classify_url(cls, url: str) -> str:
        """Classify a page type from its URL alone.

        Args:
            url: Full URL to classify.

        Returns:
            Page type string: homepage, plp, pdp, cart, checkout, account,
            policy, search, content, info, or other.
        """
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")

        if not path or path == "/":
            return "homepage"

        path_lower = path.lower()
        query = parsed.query.lower()
        full = path_lower + ("?" + query if query else "")

        for page_type, patterns in cls.URL_RULES:
            for pattern in patterns:
                if re.search(pattern, full):
                    return page_type

        return "other"

    @classmethod
    def classify_by_content(cls, title="", url="", h1="", breadcrumbs=None):
        """Classify a page type using URL plus on-page content signals.

        Falls back to content heuristics when URL classification returns 'other'.

        Args:
            title: Page title text.
            url: Page URL.
            h1: Main heading text.
            breadcrumbs: List of breadcrumb labels from the page.

        Returns:
            Page type string.
        """
        url_type = cls.classify_url(url)
        if url_type != "other":
            return url_type

        title_lower = (title or "").lower()
        crumbs = breadcrumbs or []

        if len(crumbs) >= 4:
            return "pdp"

        if any(w in title_lower for w in ["buy ", "price", "add to cart"]):
            return "pdp"

        if any(w in title_lower for w in ["shop ", "browse ", "collection"]):
            return "plp"

        if len(crumbs) == 3:
            return "plp"

        return "other"
