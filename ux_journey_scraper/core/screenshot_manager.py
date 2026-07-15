"""
Screenshot manager with PII blur capabilities.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

GRADIENT_THRESHOLD = 6.0
MIN_FILE_SIZE = 1024


def validate_screenshot(path) -> Tuple[bool, str]:
    """Validate a screenshot file for integrity and content quality.

    Returns (is_valid, error_message). error_message is empty when valid.
    """
    path = Path(path)
    if not path.exists():
        return False, "file does not exist"
    if path.stat().st_size < MIN_FILE_SIZE:
        return False, f"file too small ({path.stat().st_size} bytes)"

    try:
        img = Image.open(path)
        img.verify()
    except Exception as e:
        return False, f"PIL verify failed: {e}"

    try:
        img = Image.open(path)
        img.load()
    except Exception as e:
        return False, f"PIL load failed: {e}"

    extrema = img.convert("RGB").getextrema()
    if all(lo == hi for lo, hi in extrema):
        return False, "single-color image"

    gray = np.array(img.convert("L"), dtype=np.int16)
    total_px = gray.shape[0] * gray.shape[1]

    if total_px > 80_000_000:
        # Sample every 4th 1024-row slab
        slabs = []
        for start in range(0, gray.shape[0], 4 * 1024):
            end = min(start + 1024, gray.shape[0])
            slabs.append(gray[start:end])
        if slabs:
            gray = np.concatenate(slabs, axis=0)

    dh = np.abs(np.diff(gray, axis=1)).mean()
    dv = np.abs(np.diff(gray, axis=0)).mean()
    grad = (dh + dv) / 2.0

    if grad >= GRADIENT_THRESHOLD:
        return False, f"garbage frame (gradient={grad:.2f}, threshold={GRADIENT_THRESHOLD})"

    return True, ""


def check_suspect_blank(path) -> bool:
    """Check if a screenshot is near-blank/skeleton (warn-level, not a hard fail)."""
    path = Path(path)
    try:
        img = Image.open(path).convert("RGB")
        arr = np.array(img)
        flat = arr.reshape(-1, 3)
        total = len(flat)
        from collections import Counter
        colors = Counter(map(tuple, flat.tolist()[:100000]))
        top2 = sum(c for _, c in colors.most_common(2))
        top2_frac = top2 / min(total, 100000)
        extrema = img.getextrema()
        flat_frac = sum(1 for lo, hi in extrema if hi - lo < 5) / 3.0
        return flat_frac > 0.99 and top2_frac > 0.98
    except Exception:
        return False


class ScreenshotManager:
    """Capture screenshots and blur PII (emails, credit cards, phone numbers, names)."""

    # PII detection patterns
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    CREDIT_CARD_PATTERN = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
    PHONE_PATTERN = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
    SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

    # Common form field names that may contain PII
    PII_FIELDS = [
        "email",
        "e-mail",
        "mail",
        "password",
        "pwd",
        "pass",
        "credit-card",
        "creditcard",
        "card-number",
        "ccnumber",
        "cvv",
        "cvc",
        "security-code",
        "ssn",
        "social-security",
        "phone",
        "telephone",
        "mobile",
        "name",
        "firstname",
        "lastname",
        "fullname",
        "address",
        "street",
        "city",
        "zip",
        "zipcode",
        "postal",
    ]

    def __init__(self, output_dir="screenshots", blur_pii=True):
        """
        Initialize screenshot manager.

        Args:
            output_dir: Directory to save screenshots
            blur_pii: Whether to blur PII in screenshots
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True, parents=True)
        self.blur_pii = blur_pii

    async def capture_screenshot(self, page, step_number, blur=None):
        """
        Capture screenshot of the page.

        Args:
            page: Playwright page object
            step_number: Journey step number
            blur: Override blur_pii setting (optional)

        Returns:
            str: Path to saved screenshot
        """
        blur = blur if blur is not None else self.blur_pii

        # Generate filename
        filename = f"step-{step_number:03d}.png"
        filepath = self.output_dir / filename

        # Take screenshot
        await page.screenshot(path=str(filepath), full_page=True)

        # Validate screenshot integrity
        is_valid, error = validate_screenshot(filepath)
        if not is_valid:
            logger.warning(f"Screenshot validation failed for step {step_number}: {error}")
            filepath.unlink(missing_ok=True)
            return None

        if check_suspect_blank(filepath):
            logger.info(f"Screenshot step {step_number} flagged as suspect blank/skeleton")

        # Blur PII if enabled
        if blur:
            await self._blur_pii_in_screenshot(page, filepath)

        return str(filepath)

    async def _blur_pii_in_screenshot(self, page, screenshot_path):
        """
        Blur PII in the screenshot.

        Args:
            page: Playwright page object
            screenshot_path: Path to screenshot file
        """
        try:
            # Get text content and input fields from page
            pii_regions = await self._detect_pii_regions(page)

            if not pii_regions:
                return  # No PII detected

            # Load screenshot
            img = Image.open(screenshot_path)

            # Blur each PII region
            for region in pii_regions:
                x, y, width, height = region
                # Add some padding
                padding = 5
                x = max(0, x - padding)
                y = max(0, y - padding)
                width += 2 * padding
                height += 2 * padding

                # Extract region
                box = (x, y, x + width, y + height)
                region_img = img.crop(box)

                # Apply heavy blur
                blurred = region_img.filter(ImageFilter.GaussianBlur(radius=20))

                # Paste back
                img.paste(blurred, box)

            # Save blurred screenshot
            img.save(screenshot_path)

        except Exception as e:
            logger.warning(f"Failed to blur PII in screenshot: {e}")

    async def _detect_pii_regions(self, page):
        """
        Detect regions containing PII on the page.

        Args:
            page: Playwright page object

        Returns:
            List of tuples (x, y, width, height) representing PII regions
        """
        regions = []

        try:
            # Find input fields with PII-related names
            for field_name in self.PII_FIELDS:
                # Find by name attribute
                inputs = await page.query_selector_all(
                    f'input[name*="{field_name}"], input[id*="{field_name}"], '
                    f'input[placeholder*="{field_name}"], input[type="password"]'
                )

                for input_elem in inputs:
                    # Get bounding box
                    bbox = await input_elem.bounding_box()
                    if bbox:
                        regions.append(
                            (
                                int(bbox["x"]),
                                int(bbox["y"]),
                                int(bbox["width"]),
                                int(bbox["height"]),
                            )
                        )

            # Find text elements containing PII patterns
            # Target leaf text nodes and form-adjacent elements rather than all 'a'
            # to avoid scanning thousands of elements on large pages
            pii_selectors = "p, span, label, td, th, li, dd, dt, h1, h2, h3, h4, h5, h6, div > :not(div):not(section):not(article):not(nav)"
            all_text_elements = await page.query_selector_all(pii_selectors)
            for elem in all_text_elements:
                try:
                    text = await elem.inner_text()

                    # Check for PII patterns
                    if (
                        self.EMAIL_PATTERN.search(text)
                        or self.CREDIT_CARD_PATTERN.search(text)
                        or self.PHONE_PATTERN.search(text)
                        or self.SSN_PATTERN.search(text)
                    ):
                        bbox = await elem.bounding_box()
                        if bbox:
                            regions.append(
                                (
                                    int(bbox["x"]),
                                    int(bbox["y"]),
                                    int(bbox["width"]),
                                    int(bbox["height"]),
                                )
                            )
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"PII detection failed: {e}")

        return regions

    def create_annotated_screenshot(
        self, screenshot_path, annotations, output_path=None
    ):
        """
        Create an annotated version of a screenshot with issue markers.

        Args:
            screenshot_path: Path to original screenshot
            annotations: List of dicts with {x, y, width, height, severity, message}
            output_path: Optional output path (default: {screenshot}_annotated.png)

        Returns:
            str: Path to annotated screenshot
        """
        if output_path is None:
            path = Path(screenshot_path)
            output_path = path.parent / f"{path.stem}_annotated{path.suffix}"

        # Load screenshot
        img = Image.open(screenshot_path)
        draw = ImageDraw.Draw(img)

        # Color mapping by severity
        colors = {
            "critical": "#FF0000",  # Red
            "major": "#FFA500",  # Orange
            "minor": "#0000FF",  # Blue
        }

        # Draw annotations
        for i, annotation in enumerate(annotations, 1):
            x = annotation.get("x", 0)
            y = annotation.get("y", 0)
            width = annotation.get("width", 100)
            height = annotation.get("height", 50)
            severity = annotation.get("severity", "minor")

            color = colors.get(severity, "#0000FF")

            # Draw rectangle
            draw.rectangle([(x, y), (x + width, y + height)], outline=color, width=3)

            # Draw number marker
            marker_size = 25
            draw.ellipse(
                [(x, y - marker_size), (x + marker_size, y)], fill=color, outline=color
            )
            draw.text((x + 5, y - 20), str(i), fill="white")

        # Save annotated screenshot
        img.save(output_path)
        return str(output_path)
