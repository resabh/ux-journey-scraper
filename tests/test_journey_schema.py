"""Schema contract tests: every produced journey.json must validate.

Covers the v2.3 contract requirements:
- schema_version is 2.3
- screenshot_path / html_path written relative to journey.json's directory
- output validates against schemas/journey-schema-v2.3.json
- screenshot pixel width / DPR matches the declared viewport (defect #5)
"""

import json
from pathlib import Path

import pytest
from PIL import Image

from ux_journey_scraper.core.journey_recorder import SCHEMA_VERSION, Journey, JourneyStep
from ux_journey_scraper.core.schema_validator import (
    CURRENT_SCHEMA_VERSION,
    check_screenshot_dimensions,
    load_schema,
    validate_journey_dict,
    validate_journey_file,
)


def _build_journey(session_dir: Path, viewport=(390, 844), dpr=2) -> Path:
    """Produce a journey through the real producer code path."""
    (session_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (session_dir / "html").mkdir(parents=True, exist_ok=True)

    screenshot = session_dir / "screenshots" / "step-001.png"
    Image.new("RGB", (viewport[0] * dpr, 1200)).save(screenshot)
    html_file = session_dir / "html" / "step-1.html"
    html_file.write_text("<html><body>test</body></html>")

    journey = Journey(
        start_url="https://example.com/",
        viewport=viewport,
        platform_type="web_mobile",
        user_agent="pytest",
    )
    journey.add_step(
        JourneyStep(
            step_number=1,
            url="https://example.com/products/widget",
            title="Widget",
            screenshot_path=str(screenshot),
            page_data={
                "url": "https://example.com/products/widget",
                "title": "Widget",
                "html_path": str(html_file),
                "page_type": "pdp",
                "device_pixel_ratio": dpr,
            },
        )
    )
    journey.complete()
    journey_file = session_dir / "journey.json"
    journey.save(str(journey_file))
    return journey_file


class TestSchemaFiles:
    def test_both_schema_versions_load(self):
        assert load_schema("2.2")["properties"]["schema_version"]
        assert load_schema("2.3")["properties"]["schema_version"]["const"] == "2.3"

    def test_current_version_is_23(self):
        assert CURRENT_SCHEMA_VERSION == "2.3"
        assert SCHEMA_VERSION == "2.3"


class TestProducedJourney:
    def test_paths_relative_to_journey_json(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        data = json.loads(journey_file.read_text())
        step = data["steps"][0]
        assert step["screenshot_path"] == "screenshots/step-001.png"
        assert step["page_data"]["html_path"] == "html/step-1.html"

    def test_validates_against_v23_schema(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        assert validate_journey_file(journey_file) == []

    def test_screenshot_dimensions_match_viewport(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        assert check_screenshot_dimensions(journey_file) == []

    def test_viewport_mismatch_detected(self, tmp_path):
        # Desktop-sized screenshot on a journey claiming mobile viewport
        journey_file = _build_journey(tmp_path / "session")
        data = json.loads(journey_file.read_text())
        png = journey_file.parent / data["steps"][0]["screenshot_path"]
        Image.new("RGB", (2870, 1200)).save(png)
        errors = check_screenshot_dimensions(journey_file)
        assert len(errors) == 1
        assert "does not match" in errors[0]

    def test_absolute_paths_rejected_by_schema(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        data = json.loads(journey_file.read_text())
        data["steps"][0]["screenshot_path"] = "/absolute/path.png"
        errors = validate_journey_dict(data)
        assert any("screenshot_path" in e for e in errors)

    def test_load_resolves_relative_paths(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        journey = Journey.load(str(journey_file))
        step = journey.steps[0]
        assert Path(step.screenshot_path).is_absolute()
        assert Path(step.screenshot_path).exists()
        assert journey.steps[0].load_html() == "<html><body>test</body></html>"

    def test_old_schema_version_rejected(self, tmp_path):
        journey_file = _build_journey(tmp_path / "session")
        data = json.loads(journey_file.read_text())
        data["schema_version"] = "9.9"
        errors = validate_journey_dict(data)
        assert errors  # unknown schema version reported
