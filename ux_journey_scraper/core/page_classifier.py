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
        ("content", [r"/blog", r"/article", r"/news", r"/magazine", r"/stories", r"/sections/"]),
        # Shopify-like sites: /products/<handle> is a product detail page,
        # /collections/<handle> is a listing. pdp rules run before plp so
        # collection-scoped PDPs (/collections/x/products/y) classify as pdp.
        ("pdp", [r"/p/", r"/product/", r"/dp/", r"/item/", r"/pd/",
                 r"/products/[^/?#]+", r"\d+\.html$"]),
        ("plp", [r"/c/", r"/category/", r"/categories/", r"/shop/", r"/collection",
                 r"/products$", r"/products\?"]),
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
    def classify_page(cls, url, page_data=None):
        """Classify page type using URL patterns + DOM signals.

        Args:
            url: Page URL.
            page_data: Dict from PageAnalyzer.analyze_page() containing
                classification_signals, navigation, forms, search, title, etc.

        Returns:
            Tuple of (page_type: str, confidence: float).
        """
        url_type = cls.classify_url(url)
        signals = (page_data or {}).get("classification_signals", {})
        title = (page_data or {}).get("title", "")

        if not signals:
            conf = 0.9 if url_type != "other" else 0.3
            return (url_type, conf)

        has_atc = signals.get("has_add_to_cart", False)
        product_schemas = signals.get("product_schema_count", 0)
        card_count = signals.get("visible_product_cards", 0)
        has_filters = signals.get("has_filters", False)
        has_search_text = signals.get("has_search_results_text", False)
        crumb_depth = signals.get("breadcrumb_depth", 0)
        has_hero = signals.get("has_product_hero", False)

        # Strong DOM signals can override or confirm URL classification
        if url_type == "homepage":
            return ("homepage", 0.95)

        if url_type in ("cart", "checkout", "account", "policy", "info", "content"):
            return (url_type, 0.9)

        # PDP signals: add-to-cart + single product schema + product hero
        pdp_score = 0
        if has_atc:
            pdp_score += 3
        if product_schemas == 1:
            pdp_score += 2
        if has_hero and card_count <= 2:
            pdp_score += 1
        if crumb_depth >= 4:
            pdp_score += 1

        # PLP signals: multiple product cards + filters
        plp_score = 0
        if card_count >= 4:
            plp_score += 3
        if has_filters:
            plp_score += 2
        if product_schemas > 1:
            plp_score += 1
        if crumb_depth == 2 or crumb_depth == 3:
            plp_score += 1

        # Search signals
        search_score = 0
        if has_search_text:
            search_score += 3
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if qs.get("q") or qs.get("query") or qs.get("search"):
            search_score += 2

        if url_type == "search":
            return ("search", min(0.95, 0.8 + 0.05 * search_score))

        if url_type == "pdp":
            if plp_score > pdp_score + 2:
                return ("plp", 0.6)
            return ("pdp", min(0.95, 0.7 + 0.05 * pdp_score))

        if url_type == "plp":
            if pdp_score > plp_score + 2 and has_atc:
                return ("pdp", 0.6)
            return ("plp", min(0.95, 0.7 + 0.05 * plp_score))

        # URL said "other" — use DOM signals to determine type
        if search_score >= 3:
            return ("search", 0.6 + 0.05 * search_score)
        if pdp_score >= 3 and pdp_score > plp_score:
            return ("pdp", 0.5 + 0.05 * pdp_score)
        if plp_score >= 3 and plp_score > pdp_score:
            return ("plp", 0.5 + 0.05 * plp_score)

        # Title-based fallback
        title_lower = (title or "").lower()
        if any(w in title_lower for w in ["buy ", "price", "add to cart"]):
            return ("pdp", 0.5)
        if any(w in title_lower for w in ["shop ", "browse ", "collection"]):
            return ("plp", 0.5)

        return ("other", 0.3)

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
