"""
Smart form filling based on autocomplete attributes and field patterns.

Includes critical safety features:
- Test payment cards ONLY
- Payment submit safeguard (prevents real purchases)
- Human-like typing simulation
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional

from ux_journey_scraper.config.scrape_config import FormFillConfig
from ux_journey_scraper.core.human_behaviour import HumanBehaviour

logger = logging.getLogger(__name__)


class FormFiller:
    """Smart form filling with autocomplete detection."""

    # Global kill switch for payment submission
    PAYMENT_SUBMIT_SAFEGUARD = True  # NEVER SET TO FALSE

    NEVER_FILL = frozenset({
        "cc-name", "cc-number", "cc-exp-month", "cc-exp-year", "cc-exp", "cc-csc",
    })

    PAYMENT_FIELD_PATTERNS = frozenset({
        "card_number", "card_cvv", "card_expiry",
    })

    # Autocomplete attribute mapping (HTML5 standard)
    AUTOCOMPLETE_MAP = {
        # Name
        "name": lambda c: f"{c.first_name} {c.last_name}",
        "given-name": lambda c: c.first_name,
        "family-name": lambda c: c.last_name,
        "additional-name": lambda c: "",
        # Email & Phone
        "email": lambda c: c.email,
        "tel": lambda c: c.phone,
        "tel-national": lambda c: c.phone,
        # Address
        "street-address": lambda c: c.address_line1,
        "address-line1": lambda c: c.address_line1,
        "address-line2": lambda c: c.address_line2,
        "address-level1": lambda c: c.state,  # State/Province
        "address-level2": lambda c: c.city,  # City
        "postal-code": lambda c: c.postal_code,
        "country": lambda c: c.country,
        "country-name": lambda c: "India",
        # Organization
        "organization": lambda c: c.organization,
        "organization-title": lambda c: c.job_title,
        # Payment (TEST CARDS ONLY)
        "cc-name": lambda c: c.card_name,
        "cc-number": lambda c: c.card_number,
        "cc-exp-month": lambda c: c.card_expiry_month,
        "cc-exp-year": lambda c: c.card_expiry_year,
        "cc-exp": lambda c: f"{c.card_expiry_month}/{c.card_expiry_year}",
        "cc-csc": lambda c: c.card_cvv,
        # Birthday
        "bday-day": lambda c: c.birthday_day,
        "bday-month": lambda c: c.birthday_month,
        "bday-year": lambda c: c.birthday_year,
    }

    # Name/ID pattern regexes for fallback detection
    FIELD_PATTERNS = {
        "first_name": [r"first.*name", r"fname", r"given.*name"],
        "last_name": [r"last.*name", r"lname", r"family.*name", r"surname"],
        "email": [r"email", r"e-mail", r"mail"],
        "phone": [r"phone", r"mobile", r"telephone", r"(?<![a-z0-9])tel(?![a-z0-9])"],
        "address_line1": [r"address.*1", r"street", r"addr1"],
        "address_line2": [r"address.*2", r"apt", r"suite", r"unit"],
        "city": [r"city", r"town"],
        "state": [r"state", r"province", r"region"],
        "postal_code": [r"zip", r"postal", r"postcode"],
        "country": [r"country"],
        "card_number": [r"card.*number", r"cc.*number", r"cardnumber"],
        "card_cvv": [r"cvv", r"cvc", r"security.*code", r"csc"],
        "card_expiry": [r"expir", r"exp.*date", r"valid"],
    }

    def __init__(self, form_fill_config: FormFillConfig):
        """
        Initialize form filler.

        Args:
            form_fill_config: Form fill configuration
        """
        self.config = form_fill_config
        self.human = HumanBehaviour()

    async def fill_all_forms(self, page) -> Dict[str, any]:
        """
        Find and fill all visible forms on the page.

        Args:
            page: Playwright page

        Returns:
            Dictionary with fill results
        """
        result = {
            "forms_found": 0,
            "fields_filled": 0,
            "fields_skipped_payment": 0,
            "payment_detected": False,
            "payment_submitted": False,
        }

        if not getattr(self.config, "enabled", False):
            return result

        logger.info("Scanning for forms to fill...")

        try:
            inputs = await page.query_selector_all(
                "input:visible, select:visible, textarea:visible"
            )

            result["forms_found"] = len(inputs)

            for input_el in inputs:
                field_type = await input_el.get_attribute("type") or "text"

                if field_type in ["hidden", "submit", "button", "image", "reset"]:
                    continue

                autocomplete = await input_el.get_attribute("autocomplete") or ""
                name = await input_el.get_attribute("name") or ""
                id_attr = await input_el.get_attribute("id") or ""
                placeholder = await input_el.get_attribute("placeholder") or ""

                # Payment fields: detect and NEVER fill
                is_payment = autocomplete in self.NEVER_FILL
                if not is_payment:
                    combined = f"{name} {id_attr} {placeholder}".lower()
                    for pat_key in self.PAYMENT_FIELD_PATTERNS:
                        for pattern in self.FIELD_PATTERNS.get(pat_key, []):
                            if re.search(pattern, combined, re.IGNORECASE):
                                is_payment = True
                                break
                        if is_payment:
                            break

                if is_payment:
                    result["payment_detected"] = True
                    result["fields_skipped_payment"] += 1
                    continue

                value = self._detect_fill_value(
                    autocomplete, name, id_attr, placeholder, field_type
                )

                if value:
                    try:
                        await input_el.scroll_into_view_if_needed()
                        await input_el.click()
                        await asyncio.sleep(0.1)
                        await input_el.fill(value)
                        await self.human.human_delay(50, 150, reason="form_think")

                        result["fields_filled"] += 1
                        logger.debug(f"Filled field: {name or id_attr}")

                    except Exception as e:
                        logger.debug(f"Could not fill field {name}: {e}")

            if result["payment_detected"]:
                if self.PAYMENT_SUBMIT_SAFEGUARD:
                    logger.warning(
                        "PAYMENT FORM DETECTED - Fields filled but will NOT submit"
                    )
                    result["payment_submitted"] = False
                else:
                    raise RuntimeError(
                        "PAYMENT_SUBMIT_SAFEGUARD is disabled - refusing to continue. "
                        "This is a safety feature to prevent real payment submission."
                    )

            logger.info(
                f"Form fill complete: {result['fields_filled']} fields filled "
                f"({result['forms_found']} total fields, "
                f"{result['fields_skipped_payment']} payment fields skipped)"
            )

            return result

        except Exception as e:
            logger.error(f"Error filling forms: {e}")
            return result

    def _detect_fill_value(
        self,
        autocomplete: str,
        name: str,
        id_attr: str,
        placeholder: str,
        field_type: str,
    ) -> Optional[str]:
        """
        Detect appropriate value for a field.

        Args:
            autocomplete: autocomplete attribute value
            name: name attribute
            id_attr: id attribute
            placeholder: placeholder text
            field_type: input type

        Returns:
            Value to fill or None
        """
        # Strategy 1: Autocomplete attribute (most reliable)
        if autocomplete and autocomplete in self.AUTOCOMPLETE_MAP:
            return self.AUTOCOMPLETE_MAP[autocomplete](self.config)

        # Strategy 2: Pattern matching on name/id/placeholder
        combined = f"{name} {id_attr} {placeholder}".lower()

        for field_name, patterns in self.FIELD_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, combined, re.IGNORECASE):
                    return self._get_config_value(field_name)

        # Strategy 3: Field type-based defaults
        if field_type == "email":
            return self.config.email
        elif field_type == "tel":
            return self.config.phone
        elif field_type == "url":
            return "https://example.com"
        elif field_type == "number":
            return "1"
        elif field_type == "date":
            return "1990-06-15"

        return None

    def _get_config_value(self, field_name: str) -> Optional[str]:
        """
        Get value from config by field name.

        Args:
            field_name: Field identifier

        Returns:
            Config value or None
        """
        mapping = {
            "first_name": self.config.first_name,
            "last_name": self.config.last_name,
            "email": self.config.email,
            "phone": self.config.phone,
            "address_line1": self.config.address_line1,
            "address_line2": self.config.address_line2,
            "city": self.config.city,
            "state": self.config.state,
            "postal_code": self.config.postal_code,
            "country": self.config.country,
        }

        return mapping.get(field_name)

    async def detect_payment_form(self, page) -> bool:
        """
        Detect if page contains a payment form.

        Args:
            page: Playwright page

        Returns:
            True if payment form detected
        """
        payment_indicators = [
            'input[autocomplete*="cc-"]',
            'input[name*="card"]',
            'input[id*="card"]',
            'input[placeholder*="card"]',
            'input[name*="cvv"]',
            'input[name*="cvc"]',
        ]

        for selector in payment_indicators:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return True

        return False

    async def should_submit_form(self, page) -> bool:
        """
        Determine if a form should be submitted.

        CRITICAL: Never submit payment forms.

        Args:
            page: Playwright page

        Returns:
            True if safe to submit
        """
        # Check for payment form
        if await self.detect_payment_form(page):
            logger.warning("Payment form detected - will NOT submit")
            return False

        # Check for other risky forms
        risky_patterns = [
            "/payment",
            "/checkout",
            "/purchase",
            "/order",
            "/billing",
        ]

        url = page.url.lower()
        for pattern in risky_patterns:
            if pattern in url:
                logger.warning(f"Risky URL detected ({pattern}) - will NOT submit")
                return False

        return True
