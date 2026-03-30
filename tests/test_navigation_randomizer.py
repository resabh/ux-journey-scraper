"""Tests for NavigationRandomizer — weighted random URL ordering."""

import unittest
from collections import Counter

from ux_journey_scraper.core.navigation_randomizer import NavigationRandomizer


class TestNavigationRandomizer(unittest.TestCase):

    def setUp(self):
        self.urls = [
            {"url": "https://example.com/", "page_type": "homepage", "priority": 100},
            {"url": "https://example.com/c/shoes", "page_type": "plp", "priority": 80},
            {"url": "https://example.com/c/shirts", "page_type": "plp", "priority": 80},
            {"url": "https://example.com/p/shoe-1", "page_type": "pdp", "priority": 70},
            {"url": "https://example.com/p/shoe-2", "page_type": "pdp", "priority": 70},
            {"url": "https://example.com/cart", "page_type": "cart", "priority": 90},
            {"url": "https://example.com/privacy", "page_type": "policy", "priority": 50},
            {"url": "https://example.com/search?q=test", "page_type": "search", "priority": 65},
        ]

    def test_homepage_always_first(self):
        for _ in range(20):
            result = NavigationRandomizer.randomize(
                self.urls, session_goal="browse", target_page_types=["homepage", "plp", "pdp"]
            )
            self.assertEqual(result[0], "https://example.com/")

    def test_returns_all_urls(self):
        result = NavigationRandomizer.randomize(
            self.urls, session_goal="browse", target_page_types=["homepage", "plp", "pdp"]
        )
        self.assertEqual(len(result), len(self.urls))
        self.assertEqual(set(result), {u["url"] for u in self.urls})

    def test_target_types_favored(self):
        target_positions = []
        non_target_positions = []
        for _ in range(100):
            result = NavigationRandomizer.randomize(
                self.urls, session_goal="browse", target_page_types=["plp", "pdp"]
            )
            for i, url in enumerate(result):
                page_type = next(u["page_type"] for u in self.urls if u["url"] == url)
                if page_type in ("plp", "pdp"):
                    target_positions.append(i)
                elif page_type != "homepage":
                    non_target_positions.append(i)
        avg_target = sum(target_positions) / len(target_positions)
        avg_non_target = sum(non_target_positions) / len(non_target_positions)
        self.assertLess(avg_target, avg_non_target)

    def test_empty_urls(self):
        result = NavigationRandomizer.randomize([], session_goal="browse", target_page_types=[])
        self.assertEqual(result, [])

    def test_no_homepage(self):
        urls = [u for u in self.urls if u["page_type"] != "homepage"]
        result = NavigationRandomizer.randomize(urls, session_goal="browse", target_page_types=["plp"])
        self.assertEqual(len(result), len(urls))

    def test_different_goals_produce_different_order(self):
        cart_pos_browse = []
        cart_pos_cart = []
        for _ in range(50):
            browse_result = NavigationRandomizer.randomize(
                self.urls, session_goal="browse", target_page_types=["homepage", "plp", "pdp"]
            )
            cart_result = NavigationRandomizer.randomize(
                self.urls, session_goal="cart", target_page_types=["pdp", "cart"]
            )
            cart_pos_browse.append(browse_result.index("https://example.com/cart"))
            cart_pos_cart.append(cart_result.index("https://example.com/cart"))
        avg_browse = sum(cart_pos_browse) / len(cart_pos_browse)
        avg_cart = sum(cart_pos_cart) / len(cart_pos_cart)
        self.assertLess(avg_cart, avg_browse)


if __name__ == "__main__":
    unittest.main()
