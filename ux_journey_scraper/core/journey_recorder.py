"""
Main journey recorder engine using Playwright.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.robots_checker import RobotsChecker
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager

logger = logging.getLogger(__name__)

# Contract version written into every journey.json. The matching schema file
# lives at schemas/journey-schema-v<SCHEMA_VERSION>.json.
SCHEMA_VERSION = "2.3"


def _relativize_path(path_str, base_dir: Path) -> str:
    """Rewrite an artifact path (CWD-relative or absolute) relative to base_dir.

    v2.3 contract: screenshot_path/html_path must be relative to the directory
    containing journey.json so consumers never have to guess a base directory.
    """
    p = Path(path_str)
    absolute = p if p.is_absolute() else (Path.cwd() / p)
    try:
        return os.path.relpath(absolute.resolve(), base_dir)
    except ValueError:
        # Different drive on Windows etc. — leave untouched rather than corrupt
        return path_str


class JourneyStep:
    """Represents a single step in a user journey."""

    def __init__(
        self, step_number, url, title, screenshot_path, page_data, ux_validation=None
    ):
        self.step_number = step_number
        self.url = url
        self.title = title
        self.screenshot_path = screenshot_path
        self.page_data = page_data
        self.ux_validation = ux_validation  # UX validation results
        self.timestamp = datetime.now().isoformat()

    def load_html(self) -> str:
        """Load HTML content — from file if externalized, inline otherwise.

        Raises:
            KeyError: If step has neither html nor html_path
            FileNotFoundError: If html_path is declared but the file is missing
        """
        if "html" in self.page_data:
            return self.page_data["html"]
        html_path = self.page_data.get("html_path")
        if html_path:
            p = Path(html_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
            raise FileNotFoundError(
                f"HTML file not found for step {self.step_number}: {html_path}"
            )
        raise KeyError(f"No HTML in page_data for step {self.step_number}")

    def to_dict(self):
        """Convert step to dictionary."""
        result = {
            "step_number": self.step_number,
            "url": self.url,
            "title": self.title,
            "screenshot_path": self.screenshot_path,
            "timestamp": self.timestamp,
            "page_data": self.page_data,
        }

        # Include UX validation if available
        if self.ux_validation:
            result["ux_validation"] = self.ux_validation

        return result


class Journey:
    """Represents a complete user journey."""

    def __init__(self, start_url, viewport=(1920, 1080),
                 platform_type=None, user_agent=None, environment=None):
        self.start_url = start_url
        self.viewport = viewport
        self.platform_type = platform_type
        self.user_agent = user_agent
        self.environment = environment
        self.steps = []
        self.errors = []
        self.start_time = datetime.now().isoformat()
        self.end_time = None
        self.location_verified = None

    def add_step(self, step):
        """Add a step to the journey."""
        self.steps.append(step)

    def add_error(self, url, error, phase="unknown"):
        """Record a non-fatal error that occurred during crawl."""
        self.errors.append({
            "url": url,
            "error": str(error),
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
        })

    def complete(self):
        """Mark journey as complete."""
        self.end_time = datetime.now().isoformat()

    def to_dict(self):
        """Convert journey to dictionary."""
        result = {
            "schema_version": SCHEMA_VERSION,
            "start_url": self.start_url,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_steps": len(self.steps),
            "steps": [step.to_dict() for step in self.steps],
        }
        if self.environment:
            result["environment"] = self.environment
        if self.location_verified is not None:
            result["location_verified"] = self.location_verified
        if self.errors:
            result["errors"] = self.errors
            result["has_errors"] = True
        if self.platform_type:
            result["platform"] = {
                "type": self.platform_type,
                "user_agent": self.user_agent,
            }
        return result

    def save(self, filepath):
        """Save journey to JSON file.

        Artifact paths (screenshot_path, html_path) are rewritten relative to
        the journey.json directory per the v2.3 contract, and the result is
        validated against the schema (non-fatal — errors are logged so a long
        crawl is never lost over a validation failure).
        """
        filepath = Path(filepath)
        base_dir = filepath.resolve().parent
        data = self.to_dict()
        for step in data["steps"]:
            if step.get("screenshot_path"):
                step["screenshot_path"] = _relativize_path(
                    step["screenshot_path"], base_dir
                )
            page_data = step.get("page_data") or {}
            if page_data.get("html_path"):
                page_data["html_path"] = _relativize_path(
                    page_data["html_path"], base_dir
                )

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Journey saved to: {filepath}")

        try:
            from ux_journey_scraper.core.schema_validator import validate_journey_dict

            errors = validate_journey_dict(data)
            if errors:
                logger.warning(
                    f"journey.json FAILS schema v{data['schema_version']} "
                    f"({len(errors)} errors). First: {errors[0]}"
                )
            else:
                logger.info(
                    f"journey.json validates against schema v{data['schema_version']}"
                )
        except Exception as e:
            logger.warning(f"Schema validation skipped: {e}")

    @classmethod
    def load(cls, filepath):
        """Load journey from JSON file.

        Relative artifact paths (v2.3) are resolved against the journey.json
        directory so in-memory steps always hold usable paths.
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        base_dir = Path(filepath).resolve().parent
        for step_data in data.get("steps", []):
            sp = step_data.get("screenshot_path")
            if sp and not Path(sp).is_absolute() and (base_dir / sp).exists():
                step_data["screenshot_path"] = str(base_dir / sp)
            page_data = step_data.get("page_data") or {}
            hp = page_data.get("html_path")
            if hp and not Path(hp).is_absolute() and (base_dir / hp).exists():
                page_data["html_path"] = str(base_dir / hp)

        platform_data = data.get("platform", {})
        journey = cls(
            data["start_url"],
            (data["viewport"]["width"], data["viewport"]["height"]),
            platform_type=platform_data.get("type"),
            user_agent=platform_data.get("user_agent"),
        )
        journey.start_time = data["start_time"]
        journey.end_time = data["end_time"]
        journey.errors = data.get("errors", [])
        journey.environment = data.get("environment")
        journey.location_verified = data.get("location_verified")

        for step_data in data["steps"]:
            step = JourneyStep(
                step_data["step_number"],
                step_data["url"],
                step_data["title"],
                step_data["screenshot_path"],
                step_data["page_data"],
                ux_validation=step_data.get("ux_validation"),
            )
            step.timestamp = step_data["timestamp"]
            journey.steps.append(step)

        return journey


class JourneyRecorder:
    """Record user journeys through websites."""

    def __init__(
        self,
        start_url,
        viewport=(1920, 1080),
        blur_pii=True,
        respect_robots=True,
        headless=False,
        output_dir="journey_output",
        ux_validation_enabled=False,
        guidelines_path=None,
    ):
        """
        Initialize journey recorder.

        Args:
            start_url: Starting URL for the journey
            viewport: Viewport size as (width, height)
            blur_pii: Whether to blur PII in screenshots
            respect_robots: Whether to check robots.txt
            headless: Run browser in headless mode
            output_dir: Directory for output files
            ux_validation_enabled: Enable UX validation against Baymard guidelines
            guidelines_path: Path to processed_guidelines.json
        """
        self.start_url = start_url
        self.viewport = viewport
        self.blur_pii = blur_pii
        self.respect_robots = respect_robots
        self.headless = headless
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)

        # Initialize components
        self.screenshot_manager = ScreenshotManager(
            output_dir=self.output_dir / "screenshots", blur_pii=blur_pii
        )
        self.page_analyzer = PageAnalyzer()
        self.robots_checker = RobotsChecker() if respect_robots else None

        # Initialize UX validator if enabled
        self.ux_validator = None
        self.ux_validation_enabled = ux_validation_enabled

        if ux_validation_enabled:
            if not VALIDATORS_AVAILABLE:
                logger.warning("UX validation requested but validators not available")
                self.ux_validation_enabled = False
            elif not guidelines_path:
                logger.warning("UX validation enabled but guidelines_path not provided")
                self.ux_validation_enabled = False
            else:
                try:
                    logger.info(f"Loading Baymard guidelines from: {guidelines_path}")
                    guideline_index = GuidelineIndex(guidelines_path)
                    self.ux_validator = BaymardValidator(guideline_index)
                    stats = guideline_index.get_statistics()
                    logger.info(f"Loaded {stats['total_unique_guidelines']} guidelines")
                    logger.info(
                        f"Validation coverage: {self.ux_validator.get_validation_coverage()['coverage_percentage']}%"
                    )
                except Exception as e:
                    logger.warning(f"Failed to initialize UX validator: {e}")
                    self.ux_validation_enabled = False

        self.journey = None
        self.current_step = 0

    async def record(self):
        """
        Start recording a journey interactively.

        Returns:
            Journey: Recorded journey object
        """
        # Check robots.txt for start URL
        if self.robots_checker:
            can_proceed = self.robots_checker.check_with_confirmation(
                self.start_url, interactive=not self.headless
            )
            if not can_proceed:
                raise ValueError(
                    "Cannot proceed: robots.txt disallows and user declined."
                )

        logger.info(f"Starting journey recording: {self.start_url}")
        logger.info(f"Viewport: {self.viewport[0]}x{self.viewport[1]}")
        logger.info(f"PII Blur: {'Enabled' if self.blur_pii else 'Disabled'}")
        logger.info(f"robots.txt: {'Enabled' if self.respect_robots else 'Disabled'}")

        self.journey = Journey(self.start_url, self.viewport,
                               platform_type=getattr(self, 'platform_type', None),
                               user_agent=getattr(self, 'user_agent', None))

        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": self.viewport[0], "height": self.viewport[1]}
            )
            page = await context.new_page()

            # Go to start URL
            logger.info("Navigating to start URL...")
            await page.goto(self.start_url)
            await page.wait_for_load_state("networkidle")

            # Record first step
            await self._record_step(page)

            if not self.headless:
                # Interactive mode: wait for user navigation
                logger.info("Recording Mode Active — navigate the site, Ctrl+C to stop")

                # Listen for navigation events
                previous_url = page.url

                try:
                    while True:
                        await asyncio.sleep(1)  # Check every second

                        current_url = page.url
                        if current_url != previous_url:
                            # New page detected
                            await page.wait_for_load_state("networkidle")
                            await self._record_step(page)
                            previous_url = current_url

                except KeyboardInterrupt:
                    logger.info("Stopping recording...")

            await browser.close()

        # Complete journey
        self.journey.complete()

        logger.info(f"Journey recording complete: {len(self.journey.steps)} steps")

        return self.journey

    async def _record_step(self, page):
        """Record a single journey step."""
        self.current_step += 1

        logger.info(f"Recording Step {self.current_step}...")

        # Capture screenshot
        screenshot_path = await self.screenshot_manager.capture_screenshot(
            page, self.current_step
        )
        logger.debug(f"Screenshot: {screenshot_path}")

        # Analyze page
        page_data = await self.page_analyzer.analyze_page(page)
        logger.debug(f"Page analyzed: {page.url}")

        # Run UX validation if enabled
        ux_validation = None
        if self.ux_validation_enabled and self.ux_validator:
            try:
                logger.debug("Running UX validation...")
                ux_validation = self.ux_validator.validate_page(
                    url=page.url,
                    html=page_data["html"],
                    title=page_data["title"],
                    screenshot_path=screenshot_path,
                    page_data=page_data,
                )

                score = ux_validation["compliance_score"]
                violations_count = len(ux_validation["violations"])
                warnings_count = len(ux_validation["warnings"])

                logger.info(
                    f"UX Compliance: {score}% ({ux_validation['page_type']})"
                )

                if violations_count > 0:
                    logger.info(f"{violations_count} violation(s) found")
                if warnings_count > 0:
                    logger.info(f"{warnings_count} warning(s)")

            except Exception as e:
                logger.warning(f"UX validation failed: {e}")

        # Create step
        step = JourneyStep(
            step_number=self.current_step,
            url=page.url,
            title=await page.title(),
            screenshot_path=screenshot_path,
            page_data=page_data,
            ux_validation=ux_validation,
        )

        # Add to journey
        self.journey.add_step(step)

        logger.info(f"Step {self.current_step} recorded: {step.title}")

    async def record_automated(self, urls):
        """
        Record a journey through a predefined list of URLs.

        Args:
            urls: List of URLs to visit

        Returns:
            Journey: Recorded journey object
        """
        logger.info(f"Starting automated journey recording: {len(urls)} URLs")

        self.journey = Journey(urls[0], self.viewport,
                               platform_type=getattr(self, 'platform_type', None),
                               user_agent=getattr(self, 'user_agent', None))

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                viewport={"width": self.viewport[0], "height": self.viewport[1]}
            )
            page = await context.new_page()

            for i, url in enumerate(urls, 1):
                # Check robots.txt
                if self.robots_checker:
                    can_proceed = self.robots_checker.check_with_confirmation(
                        url, interactive=False  # Non-interactive for automated
                    )
                    if not can_proceed:
                        logger.warning(f"Skipping {url} (robots.txt)")
                        continue

                logger.info(f"[{i}/{len(urls)}] Navigating to: {url}")

                try:
                    await page.goto(url, timeout=30000)
                    await page.wait_for_load_state("networkidle", timeout=10000)

                    # Record step
                    await self._record_step(page)

                    # Small delay between pages
                    await asyncio.sleep(2)

                except Exception as e:
                    logger.warning(f"Error loading {url}: {e}")
                    continue

            await browser.close()

        self.journey.complete()

        logger.info(f"Automated journey recording complete: {len(self.journey.steps)} steps")

        return self.journey
