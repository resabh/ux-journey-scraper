"""Weighted random URL ordering based on session goal.

Ensures navigation order looks human: homepage first, session-relevant
pages favored, non-relevant pages interspersed naturally.
"""

import random
from typing import List

ADJACENT_TYPES = {
    "browse": ["search", "content", "info"],
    "search": ["plp", "pdp"],
    "cart": ["plp", "checkout"],
    "checkout": ["cart", "account"],
    "post_order": ["account"],
    "fill_gaps": [],
}


class NavigationRandomizer:
    """Weighted random URL ordering based on session goal."""

    @staticmethod
    def randomize(
        urls: List[dict],
        session_goal: str,
        target_page_types: List[str],
    ) -> List[str]:
        """Return URLs in weighted random order.

        Rules:
        - Homepage always first (anchor point)
        - Pages matching target_page_types: 3x weight
        - Adjacent page types for this goal: 1.5x weight
        - Other pages: 1x weight

        Args:
            urls: From PageSelector — list of dicts with 'url' and 'page_type'.
            session_goal: Current session goal (browse, search, cart, etc.).
            target_page_types: Page types this session should prioritize.

        Returns:
            List of URL strings in weighted random order.
        """
        if not urls:
            return []

        homepage = None
        rest = []
        for item in urls:
            if item.get("page_type") == "homepage":
                homepage = item["url"]
            else:
                rest.append(item)

        adjacent = set(ADJACENT_TYPES.get(session_goal, []))

        weighted = []
        for item in rest:
            page_type = item.get("page_type", "other")
            if page_type in target_page_types:
                weight = 3.0
            elif page_type in adjacent:
                weight = 1.5
            else:
                weight = 1.0
            weighted.append((item["url"], weight))

        shuffled = sorted(
            weighted,
            key=lambda x: x[1] * random.random(),
            reverse=True,
        )

        result = [url for url, _ in shuffled]

        if homepage:
            result.insert(0, homepage)

        return result
