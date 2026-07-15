"""
Journey file loader - reads journey.json and provides page data.

Loads journey files created by ux-journey-scraper for offline analysis.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image


@dataclass
class JourneyStep:
    """
    Single step from a journey.

    Represents one page visit in a recorded user journey.
    """

    step_number: int
    url: str
    title: str
    screenshot_path: Path
    timestamp: str
    page_data: Dict
    ux_validation: Optional[Dict] = None
    journey_dir: Optional[Path] = None

    def load_screenshot(self) -> Image.Image:
        """
        Load screenshot image.

        Returns:
            PIL Image object

        Raises:
            FileNotFoundError: If screenshot file doesn't exist
        """
        if not self.screenshot_path.exists():
            raise FileNotFoundError(f"Screenshot not found: {self.screenshot_path}")

        return Image.open(self.screenshot_path)

    def load_html(self) -> str:
        """Load HTML content.

        Resolution order:
        1. Inline page_data["html"] (no I/O)
        2. page_data["html_path"] resolved against journey_dir
           with bounded ancestor walk-up (≤6 levels)

        Raises:
            KeyError: If step has NEITHER html nor html_path
            FileNotFoundError: If html_path is declared but the file is missing
        """
        if "html" in self.page_data:
            return self.page_data["html"]

        html_path_str = self.page_data.get("html_path")
        if not html_path_str:
            raise KeyError(f"No HTML in page_data for step {self.step_number}")

        html_path = Path(html_path_str)
        if html_path.is_absolute() and html_path.exists():
            return html_path.read_text(encoding="utf-8")

        if self.journey_dir:
            candidate = self.journey_dir / html_path
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")

            # Bounded ancestor walk-up for legacy CWD-relative paths
            base = self.journey_dir
            for _ in range(6):
                base = base.parent
                candidate = base / html_path
                if candidate.exists():
                    return candidate.read_text(encoding="utf-8")

        raise FileNotFoundError(
            f"HTML file not found for step {self.step_number}: {html_path_str}"
        )

    def get_forms(self) -> List[Dict]:
        """Get form data from page_data."""
        return self.page_data.get("forms", [])

    def get_links(self) -> List[Dict]:
        """Get links from page_data."""
        return self.page_data.get("links", [])

    def get_buttons(self) -> List[Dict]:
        """Get buttons from page_data."""
        return self.page_data.get("buttons", [])

    def get_ctas(self) -> List[Dict]:
        """Get CTAs from page_data."""
        return self.page_data.get("ctas", [])

    def get_navigation(self) -> Dict:
        """Get navigation data from page_data."""
        return self.page_data.get("navigation", {})

    def get_search(self) -> Dict:
        """Get search bar data from page_data."""
        return self.page_data.get("search", {})

    def get_meta(self) -> Dict:
        """Get meta tags from page_data."""
        return self.page_data.get("meta", {})


class JourneyLoader:
    """
    Loads and parses journey files.

    Usage:
        loader = JourneyLoader("./data/journey.json")
        steps = loader.get_steps()

        for step in steps:
            html = step.load_html()
            screenshot = step.load_screenshot()
            # Analyze...
    """

    def __init__(self, journey_path: Path):
        """
        Initialize loader.

        Args:
            journey_path: Path to journey.json file

        Raises:
            FileNotFoundError: If journey file doesn't exist
        """
        self.journey_path = Path(journey_path)
        if not self.journey_path.exists():
            raise FileNotFoundError(f"Journey file not found: {self.journey_path}")

        self.journey_dir = self.journey_path.parent
        self._journey_data = None

    def load(self) -> Dict:
        """
        Load journey.json file.

        Returns:
            Journey data dictionary

        Raises:
            json.JSONDecodeError: If file is not valid JSON
        """
        with open(self.journey_path) as f:
            self._journey_data = json.load(f)

        return self._journey_data

    def get_steps(self) -> List[JourneyStep]:
        """
        Get all journey steps.

        Returns:
            List of JourneyStep objects

        Raises:
            ValueError: If journey data is invalid
        """
        if not self._journey_data:
            self.load()

        steps = []

        if "steps" not in self._journey_data:
            raise ValueError("Journey data missing 'steps' array")

        for step_data in self._journey_data["steps"]:
            # Resolve screenshot path (relative to journey.json)
            screenshot_path = self.journey_dir / step_data["screenshot_path"]

            step = JourneyStep(
                step_number=step_data["step_number"],
                url=step_data["url"],
                title=step_data.get("title", ""),
                screenshot_path=screenshot_path,
                timestamp=step_data.get("timestamp", ""),
                page_data=step_data.get("page_data", {}),
                ux_validation=step_data.get("ux_validation"),
                journey_dir=self.journey_dir,
            )
            steps.append(step)

        return steps

    def get_step(self, step_number: int) -> Optional[JourneyStep]:
        """
        Get a specific step by number.

        Args:
            step_number: Step number to retrieve

        Returns:
            JourneyStep or None if not found
        """
        steps = self.get_steps()
        for step in steps:
            if step.step_number == step_number:
                return step
        return None

    def get_start_url(self) -> str:
        """
        Get journey start URL.

        Returns:
            Start URL
        """
        if not self._journey_data:
            self.load()
        return self._journey_data.get("start_url", "")

    def get_total_steps(self) -> int:
        """
        Get total number of steps.

        Returns:
            Number of steps
        """
        if not self._journey_data:
            self.load()
        return self._journey_data.get("total_steps", 0)

    def get_metadata(self) -> Dict:
        """
        Get journey metadata.

        Returns:
            Dictionary with start_time, end_time, viewport, etc.
        """
        if not self._journey_data:
            self.load()

        return {
            "start_url": self._journey_data.get("start_url"),
            "start_time": self._journey_data.get("start_time"),
            "end_time": self._journey_data.get("end_time"),
            "viewport": self._journey_data.get("viewport"),
            "platform": self._journey_data.get("platform"),
            "total_steps": self._journey_data.get("total_steps"),
        }
