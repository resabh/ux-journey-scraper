"""Validate journey.json output against the published schema contract.

The schema files live in <repo>/schemas/journey-schema-v<version>.json and are
the formal contract between this scraper (producer) and downstream consumers.
Producers MUST validate every journey.json they emit; CI runs the same checks.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
CURRENT_SCHEMA_VERSION = "2.3"

_SCHEMA_CACHE = {}


def load_schema(version: str = CURRENT_SCHEMA_VERSION) -> dict:
    """Load (and cache) the JSON schema for a given contract version."""
    if version not in _SCHEMA_CACHE:
        path = SCHEMA_DIR / f"journey-schema-v{version}.json"
        if not path.exists():
            raise FileNotFoundError(f"No schema for version {version}: {path}")
        _SCHEMA_CACHE[version] = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[version]


def validate_journey_dict(data: dict, version: str = None) -> list:
    """Validate a journey dict against its declared schema version.

    Args:
        data: Parsed journey.json content.
        version: Schema version override. Defaults to data["schema_version"].

    Returns:
        List of human-readable error strings. Empty list means valid.
    """
    import jsonschema

    version = version or data.get("schema_version") or CURRENT_SCHEMA_VERSION
    try:
        schema = load_schema(version)
    except FileNotFoundError as e:
        return [str(e)]

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def validate_journey_file(path) -> list:
    """Validate a journey.json file. Returns list of errors (empty = valid)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"unreadable journey file: {e}"]
    return validate_journey_dict(data)


def check_screenshot_dimensions(journey_file, data: dict = None, tolerance: float = 0.1) -> list:
    """Check that screenshots match the declared viewport (defect #5 acceptance).

    A screenshot's pixel width divided by the step's device_pixel_ratio must
    equal the journey's declared viewport width (within tolerance). Catches
    crawls where the browser context ignored the configured viewport and
    rendered desktop layouts for a "mobile" journey.

    Returns:
        List of error strings. Empty list means all screenshots consistent.
    """
    from PIL import Image

    journey_file = Path(journey_file)
    if data is None:
        try:
            data = json.loads(journey_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            return [f"unreadable journey file: {e}"]

    vw = (data.get("viewport") or {}).get("width")
    if not vw:
        return ["viewport.width missing — cannot check screenshot dimensions"]

    errors = []
    for step in data.get("steps", []):
        sp = step.get("screenshot_path")
        if not sp:
            continue
        p = Path(sp)
        if not p.is_absolute():
            p = journey_file.parent / p
        if not p.exists():
            errors.append(f"step {step.get('step_number')}: screenshot not found: {sp}")
            continue
        try:
            with Image.open(p) as img:
                px_width = img.width
        except Exception as e:
            errors.append(f"step {step.get('step_number')}: unreadable PNG {sp}: {e}")
            continue
        dpr = (step.get("page_data") or {}).get("device_pixel_ratio") or 1
        css_width = px_width / dpr
        if abs(css_width - vw) > tolerance * vw:
            errors.append(
                f"step {step.get('step_number')}: screenshot CSS width "
                f"{css_width:.0f} ({px_width}px / DPR {dpr}) does not match "
                f"declared viewport width {vw}"
            )
    return errors
