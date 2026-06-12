"""Tests for the journey coverage reporter."""

import json
from pathlib import Path

import pytest

from ux_journey_scraper.core.coverage_reporter import CoverageReporter


def _write_journey(session_dir: Path, platform: str, steps: list):
    session_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": "2.3",
        "start_url": "https://example.com/",
        "viewport": {"width": 390, "height": 844},
        "platform": {"type": platform, "user_agent": None},
        "total_steps": len(steps),
        "steps": [
            {
                "step_number": i + 1,
                "url": s["url"],
                "title": s.get("title", ""),
                "screenshot_path": f"screenshots/step-{i+1:03d}.png",
                "page_data": {
                    "url": s["url"],
                    "html_path": f"html/step-{i+1}.html",
                    **{k: v for k, v in s.items() if k != "url"},
                },
            }
            for i, s in enumerate(steps)
        ],
    }
    (session_dir / "journey.json").write_text(json.dumps(data))


@pytest.fixture
def run_dir(tmp_path):
    _write_journey(
        tmp_path / "visit_web_mobile_browse_00",
        "web_mobile",
        [
            {"url": "https://example.com/", "page_type": "homepage"},
            {"url": "https://example.com/collections/all", "page_type": "plp"},
            {"url": "https://example.com/products/widget", "page_type": "pdp"},
            {"url": "https://example.com/pages/privacy-policy", "page_type": "policy"},
        ],
    )
    _write_journey(
        tmp_path / "flows_web_mobile",
        "web_mobile",
        [
            {"url": "https://example.com/products/widget", "page_type": "pdp", "flow": "pdp_view"},
            {
                "url": "https://example.com/products/widget",
                "page_type": "pdp",
                "flow": "add_to_cart",
            },
            {
                "url": "https://example.com/cart",
                "page_type": "cart",
                "flow": "cart_with_items",
                "cart_item_count": 1,
            },
            {
                "url": "https://example.com/checkouts/c/abc",
                "page_type": "checkout",
                "flow": "checkout_start",
            },
            {
                "url": "https://example.com/search?q=widget",
                "page_type": "search",
                "flow": "search_results",
            },
            {
                "url": "https://example.com/account/login",
                "page_type": "account",
                "flow": "login_page",
            },
        ],
    )
    return tmp_path


class TestCoverageReporter:
    def test_found_and_missed(self, run_dir):
        report = CoverageReporter().evaluate(run_dir, write=False)
        by_id = {r["id"]: r for r in report["journeys"]}

        assert by_id["homepage"]["status"] == "found"
        assert by_id["browse_plp"]["status"] == "found"
        assert by_id["browse_pdp"]["status"] == "found"
        assert by_id["add_to_cart"]["status"] == "found"
        assert by_id["cart_with_items"]["status"] == "found"
        assert by_id["checkout_start"]["status"] == "found"
        assert by_id["search_results"]["status"] == "found"
        assert by_id["login_account"]["status"] == "found"
        assert by_id["policy_pages"]["status"] == "found"
        # Nothing captured wishlist or order tracking
        assert by_id["wishlist"]["status"] == "missed"
        assert by_id["order_tracking"]["status"] == "missed"

    def test_empty_cart_does_not_count(self, tmp_path):
        _write_journey(
            tmp_path / "flows_web_mobile",
            "web_mobile",
            [
                {
                    "url": "https://example.com/cart",
                    "page_type": "cart",
                    "flow": "cart_empty",
                    "cart_item_count": 0,
                },
            ],
        )
        report = CoverageReporter().evaluate(tmp_path, write=False)
        by_id = {r["id"]: r for r in report["journeys"]}
        assert by_id["cart_with_items"]["status"] == "missed"

    def test_writes_artifacts(self, run_dir):
        report = CoverageReporter().evaluate(run_dir)
        assert (run_dir / "coverage.json").exists()
        assert (run_dir / "coverage.md").exists()
        saved = json.loads((run_dir / "coverage.json").read_text())
        assert saved["summary"] == report["summary"]
        table = (run_dir / "coverage.md").read_text()
        assert "web_mobile" in table
        assert "MISSED" in table

    def test_site_specific_journeys(self, run_dir):
        class FakeCoverage:
            enabled = True
            run_flows = True
            search_term = "widget"
            site_journeys = [
                {"id": "gst_bulk", "label": "GST / bulk ordering", "url_patterns": ["gst", "bulk"]}
            ]

        class FakeConfig:
            target = {"name": "Example"}
            coverage = FakeCoverage()

        report = CoverageReporter(FakeConfig()).evaluate(run_dir, write=False)
        by_id = {r["id"]: r for r in report["journeys"]}
        assert by_id["gst_bulk"]["status"] == "missed"
        assert by_id["gst_bulk"]["site_specific"] is True

    def test_evidence_includes_platform_and_url(self, run_dir):
        report = CoverageReporter().evaluate(run_dir, write=False)
        by_id = {r["id"]: r for r in report["journeys"]}
        ev = by_id["cart_with_items"]["evidence"][0]
        assert ev["platform"] == "web_mobile"
        assert ev["url"] == "https://example.com/cart"
        assert ev["matched_by"] == "flow:cart_with_items"
