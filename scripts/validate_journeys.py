#!/usr/bin/env python3
"""Validate journey.json files against the schema contract (CI entry point).

Usage:
    python scripts/validate_journeys.py PATH [PATH...]   # validate files/dirs
    python scripts/validate_journeys.py --self-test      # producer self-test

The self-test builds a journey through the real producer code path
(Journey/JourneyStep + Journey.save) in a temp directory and validates the
output against the current schema, including the screenshot-vs-viewport
dimension check. This is what CI runs on every push: if producer code drifts
from the contract, the build fails.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def self_test() -> int:
    from PIL import Image

    from ux_journey_scraper.core.journey_recorder import SCHEMA_VERSION, Journey, JourneyStep
    from ux_journey_scraper.core.schema_validator import (
        check_screenshot_dimensions,
        validate_journey_dict,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        session_dir = tmp / "session"
        (session_dir / "screenshots").mkdir(parents=True)
        (session_dir / "html").mkdir(parents=True)

        # Produce artifacts exactly the way the crawler does
        viewport = (390, 844)
        dpr = 2
        screenshot = session_dir / "screenshots" / "step-001.png"
        Image.new("RGB", (viewport[0] * dpr, 1600)).save(screenshot)
        html_file = session_dir / "html" / "step-1.html"
        html_file.write_text("<html><body>self-test</body></html>")

        journey = Journey(
            start_url="https://example.com/",
            viewport=viewport,
            platform_type="web_mobile",
            user_agent="self-test",
        )
        journey.add_step(
            JourneyStep(
                step_number=1,
                url="https://example.com/products/test-product",
                title="Test product",
                screenshot_path=str(screenshot),
                page_data={
                    "url": "https://example.com/products/test-product",
                    "title": "Test product",
                    "html_path": str(html_file),
                    "page_type": "pdp",
                    "device_pixel_ratio": dpr,
                },
            )
        )
        journey.complete()

        journey_file = session_dir / "journey.json"
        journey.save(str(journey_file))

        data = json.loads(journey_file.read_text())
        failures = []

        if data["schema_version"] != SCHEMA_VERSION:
            failures.append(
                f"schema_version is {data['schema_version']}, expected {SCHEMA_VERSION}"
            )

        step = data["steps"][0]
        if step["screenshot_path"] != "screenshots/step-001.png":
            failures.append(
                f"screenshot_path not relative to journey.json: {step['screenshot_path']}"
            )
        if step["page_data"]["html_path"] != "html/step-1.html":
            failures.append(
                f"html_path not relative to journey.json: {step['page_data']['html_path']}"
            )

        failures += validate_journey_dict(data)
        failures += check_screenshot_dimensions(journey_file, data)

        if failures:
            print(f"SELF-TEST FAILED ({len(failures)} errors):")
            for f in failures:
                print(f"  - {f}")
            return 1

        print(f"Self-test passed: producer output validates against schema v{SCHEMA_VERSION}")
        return 0


def validate_paths(paths) -> int:
    from ux_journey_scraper.core.schema_validator import (
        check_screenshot_dimensions,
        validate_journey_file,
    )

    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files.extend(sorted(p.glob("**/journey.json")))
        elif p.exists():
            files.append(p)

    if not files:
        print("No journey.json files found.")
        return 1

    failed = 0
    for f in files:
        errors = validate_journey_file(f)
        errors += check_screenshot_dimensions(f)
        if errors:
            failed += 1
            print(f"FAIL {f} ({len(errors)} errors)")
            for e in errors[:10]:
                print(f"     - {e}")
        else:
            print(f"OK   {f}")

    print(f"\n{len(files) - failed}/{len(files)} journey files valid")
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="journey.json files or directories")
    parser.add_argument("--self-test", action="store_true", help="run producer self-test")
    args = parser.parse_args()

    if args.self_test:
        sys.exit(self_test())
    if not args.paths:
        parser.error("provide PATHs or --self-test")
    sys.exit(validate_paths(args.paths))


if __name__ == "__main__":
    main()
