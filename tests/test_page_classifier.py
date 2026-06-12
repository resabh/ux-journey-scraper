"""Tests for page type classification."""
import pytest
from ux_journey_scraper.core.page_classifier import PageClassifier


class TestURLClassification:
    def test_homepage(self):
        assert PageClassifier.classify_url("https://example.com/") == "homepage"
        assert PageClassifier.classify_url("https://example.com") == "homepage"

    def test_plp(self):
        assert PageClassifier.classify_url("https://example.com/c/shoes") == "plp"
        assert PageClassifier.classify_url("https://example.com/category/men") == "plp"
        assert PageClassifier.classify_url("https://example.com/shop/electronics") == "plp"

    def test_pdp(self):
        assert PageClassifier.classify_url("https://example.com/p/blue-shoes-123") == "pdp"
        assert PageClassifier.classify_url("https://example.com/product/abc123") == "pdp"
        assert PageClassifier.classify_url("https://example.com/dp/B09V3KXJPB") == "pdp"

    def test_cart(self):
        assert PageClassifier.classify_url("https://example.com/cart") == "cart"
        assert PageClassifier.classify_url("https://example.com/bag") == "cart"

    def test_checkout(self):
        assert PageClassifier.classify_url("https://example.com/checkout") == "checkout"

    def test_account(self):
        assert PageClassifier.classify_url("https://example.com/login") == "account"
        assert PageClassifier.classify_url("https://example.com/signup") == "account"
        assert PageClassifier.classify_url("https://example.com/myaccount/orders") == "account"

    def test_policy(self):
        assert PageClassifier.classify_url("https://example.com/privacy-policy") == "policy"
        assert PageClassifier.classify_url("https://example.com/terms-of-use") == "policy"

    def test_search(self):
        assert PageClassifier.classify_url("https://example.com/search?q=shoes") == "search"

    def test_info(self):
        assert PageClassifier.classify_url("https://example.com/about") == "info"
        assert PageClassifier.classify_url("https://example.com/contact-us") == "info"
        assert PageClassifier.classify_url("https://example.com/faq") == "info"

    def test_other(self):
        assert PageClassifier.classify_url("https://example.com/xyz/abc") == "other"

    def test_shopify_pdp(self):
        # Corpus v1 defect #1: /products/<handle> was mislabeled plp
        assert PageClassifier.classify_url(
            "https://www.boat-lifestyle.com/products/airdopes-131"
        ) == "pdp"
        assert PageClassifier.classify_url(
            "https://example.com/collections/speakers/products/stone-1200"
        ) == "pdp"
        assert PageClassifier.classify_url(
            "https://example.com/products/widget?variant=123"
        ) == "pdp"

    def test_shopify_plp(self):
        assert PageClassifier.classify_url(
            "https://www.boat-lifestyle.com/collections/true-wireless-earbuds"
        ) == "plp"
        assert PageClassifier.classify_url("https://example.com/collections") == "plp"
        assert PageClassifier.classify_url("https://example.com/products") == "plp"
        assert PageClassifier.classify_url("https://example.com/products?page=2") == "plp"


class TestContentClassification:
    def test_pdp_by_content(self):
        result = PageClassifier.classify_by_content(
            title="Blue Running Shoes - Buy Online",
            url="https://example.com/p/abc123",
            h1="Blue Running Shoes",
            breadcrumbs=["Home", "Shoes", "Running", "Blue Running Shoes"],
        )
        assert result == "pdp"

    def test_plp_by_content(self):
        result = PageClassifier.classify_by_content(
            title="Men's Shoes - Shop Online",
            url="https://example.com/c/mens-shoes",
            h1="Men's Shoes",
            breadcrumbs=["Home", "Men", "Shoes"],
        )
        assert result == "plp"
