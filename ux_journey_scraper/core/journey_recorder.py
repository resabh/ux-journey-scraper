"""
Main journey recorder engine using Playwright.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright
from ux_journey_scraper.core.page_analyzer import PageAnalyzer
from ux_journey_scraper.core.robots_checker import RobotsChecker
from ux_journey_scraper.core.screenshot_manager import ScreenshotManager

logger = logging.getLogger(__name__)


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
        """Load HTML content — from file if externalized, inline otherwise."""
        html_path = self.page_data.get("html_path")
        if html_path:
            p = Path(html_path)
            if p.exists():
                return p.read_text(encoding="utf-8")
        return self.page_data.get("html", "")

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
                 platform_type=None, user_agent=None):
        self.start_url = start_url
        self.viewport = viewport
        self.platform_type = platform_type
        self.user_agent = user_agent
        self.steps = []
        self.errors = []
        self.start_time = datetime.now().isoformat()
        self.end_time = None

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
            "schema_version": "2.2",
            "start_url": self.start_url,
            "viewport": {"width": self.viewport[0], "height": self.viewport[1]},
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_steps": len(self.steps),
            "steps": [step.to_dict() for step in self.steps],
        }
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
        """Save journey to JSON file."""
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Journey saved to: {filepath}")

    @classmethod
    def load(cls, filepath):
        """Load journey from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)

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
