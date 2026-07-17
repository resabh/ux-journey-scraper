"""Shared session-precondition helpers used by BOTH crawl paths.

Standing rule (h027): logic needed by more than one crawl path lands in a
shared module FIRST. This module is the shared seam. Stage 2 (h027) will
consolidate stealth/dismissal here too; for now it holds occlusion
measurement (S1.12 mechanism 4) and location establishment (S1.6).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LocationError(Exception):
    """Raised when location.enforce=True and PDP verification fails —
    the run-level circuit breaker (S1.6 mechanism 1)."""

# A step is considered occluded if a single overlay covers more than this
# fraction of the viewport (large centered modal → step not benchmark-ready).
OCCLUSION_THRESHOLD = 0.30

# JS that measures the largest viewport-covering fixed/sticky/absolute overlay.
# Paint-aware: ignores hidden/transparent/zero-size elements and only counts
# the intersection of the element's rect with the viewport.
_OCCLUSION_JS = """
() => {
    const vw = window.innerWidth, vh = window.innerHeight;
    const vArea = vw * vh;
    if (!vArea) return 0;
    let maxCover = 0;
    const els = document.querySelectorAll('*');
    for (const el of els) {
        const cs = window.getComputedStyle(el);
        const pos = cs.position;
        if (pos !== 'fixed' && pos !== 'sticky' && pos !== 'absolute') continue;
        // Paint-aware: skip invisible elements
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const op = parseFloat(cs.opacity || '1');
        if (op < 0.1) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        // Intersection with viewport
        const ix = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
        const iy = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
        const inter = ix * iy;
        if (inter <= 0) continue;
        const cover = inter / vArea;
        // Ignore full-page containers (roots that legitimately fill the page
        // but are not overlays): require a nonzero z-index or a background.
        const z = parseInt(cs.zIndex || '0', 10);
        const hasBg = cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)'
                      && cs.backgroundColor !== 'transparent';
        if (cover > 0.95 && !(z > 0) && !hasBg) continue;
        if (cover > maxCover) maxCover = cover;
    }
    return maxCover;
}
"""


async def measure_occlusion(page) -> float:
    """Return the max fraction (0..1) of the viewport covered by a single
    fixed/sticky/absolute overlay element. 0.0 on any failure (fail-open for
    measurement — the readiness gate decides based on the recorded value)."""
    try:
        cover = await page.evaluate(_OCCLUSION_JS)
        return float(cover or 0.0)
    except Exception as e:
        logger.debug(f"Occlusion measurement failed: {e}")
        return 0.0


async def record_occlusion(page, page_data: dict) -> None:
    """Measure overlay coverage and record it into page_data.

    Sets page_data['overlay_coverage'] (float) and page_data['occluded'] (bool)
    per the OCCLUSION_THRESHOLD. Called immediately before screenshot capture on
    both crawl paths so the recorded value reflects what the screenshot shows.
    """
    coverage = await measure_occlusion(page)
    page_data["overlay_coverage"] = round(coverage, 4)
    page_data["occluded"] = coverage > OCCLUSION_THRESHOLD


# --- Location establishment (S1.6) — the ONE shared implementation ------------

_PRODUCT_SIGNAL_JS = """() => {
    const atc = document.querySelector('[class*="add-to-cart" i], [class*="addtocart" i], button[id*="add-to-cart" i]');
    const schema = document.querySelector('script[type="application/ld+json"]');
    const price = document.querySelector('[class*="price" i], [class*="selling" i]');
    const productName = document.querySelector('[class*="product-name" i], [class*="product-title" i], h1');
    return !!(atc || schema || price || productName);
}"""


def _selectors(location_cfg, attr, default):
    vals = getattr(location_cfg, attr, None)
    return list(vals) if vals else list(default)


async def location_panel_visible(page, location_cfg) -> bool:
    """Return True if a location/address panel is currently visible."""
    panel_sels = _selectors(location_cfg, "panel_selectors", (
        '[class*="AddressFullscreenModal" i]',
        '[class*="address-modal" i]',
        '[class*="location-modal" i]',
        '[class*="pincode-modal" i]',
    ))
    for sel in panel_sels:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            continue
    trigger = getattr(location_cfg, "manual_trigger_text", "") or "Select Location Manually"
    try:
        el = page.get_by_text(trigger)
        if await el.count() and await el.is_visible():
            return True
    except Exception:
        pass
    return False


async def fill_location(page, location_cfg) -> bool:
    """FILL the location panel with the configured pincode and confirm.

    Returns True if the fill flow completed (suggestion + confirm clicked).
    Stateless — used by both crawl paths (S1.6 mechanism 3).
    """
    pincode = getattr(location_cfg, "pincode", "")
    if not pincode:
        return False

    trigger = getattr(location_cfg, "manual_trigger_text", "") or "Select Location Manually"
    try:
        manual_btn = page.get_by_text(trigger)
        if await manual_btn.count() and await manual_btn.is_visible():
            await manual_btn.click(timeout=5000)
            await page.wait_for_timeout(1500)
    except Exception as e:
        logger.debug(f"Location fill: manual trigger click: {e}")

    input_sels = _selectors(location_cfg, "input_selectors", (
        "input.pac-target-input",
        'input[placeholder*="area" i]',
        'input[placeholder*="location" i]',
        'input[placeholder*="pincode" i]',
        'input[placeholder*="search" i]',
    ))
    places_input = None
    for sel in input_sels:
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                places_input = loc
                break
        except Exception:
            continue
    if places_input is None:
        logger.warning("Location fill: no location input found")
        return False
    try:
        await places_input.fill("")
        await places_input.type(pincode, delay=80)
        await page.wait_for_timeout(2000)
    except Exception as e:
        logger.warning(f"Location fill: could not type pincode: {e}")
        return False

    suggestion_sels = _selectors(location_cfg, "suggestion_selectors", (
        ".pac-item", '[class*="pac-item"]', '[class*="suggestion" i]',
    ))
    suggestion_clicked = False
    for sel in suggestion_sels:
        try:
            s = page.locator(sel).first
            if await s.count() and await s.is_visible():
                await s.click(timeout=5000)
                suggestion_clicked = True
                break
        except Exception:
            continue
    if not suggestion_clicked:
        logger.warning("Location fill: no autocomplete suggestion appeared")
        return False
    await page.wait_for_timeout(1500)

    confirm_sels = _selectors(location_cfg, "confirm_selectors", (
        'button.AddressFullscreenModal__confirm-button',
        'button:has-text("Confirm Location")',
        'button:has-text("Confirm")',
        '[class*="confirm" i] button',
    ))
    confirm_clicked = False
    for sel in confirm_sels:
        try:
            c = page.locator(sel).first
            if await c.count() and await c.is_visible():
                await c.click(timeout=5000)
                confirm_clicked = True
                break
        except Exception:
            continue
    if not confirm_clicked:
        logger.warning("Location fill: could not click confirm button")
        return False

    await page.wait_for_timeout(4000)
    logger.info(f"Location fill: completed for pincode {pincode}")
    return True


async def verify_location(page, location_cfg) -> bool:
    """Verify location is established by loading the configured known PDP and
    checking for product signals. Returns True if verified. When no verify_url
    is configured, returns True (nothing to verify against)."""
    verify_url = getattr(location_cfg, "verify_url", "") or ""
    if not verify_url:
        logger.info("Location verify: no verify_url configured, treating as verified")
        return True

    for attempt in range(2):
        try:
            resp = await page.goto(verify_url, timeout=30000, wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception:
                pass
            has_product = await page.evaluate(_PRODUCT_SIGNAL_JS)
            if has_product and resp and resp.status < 400:
                logger.info(f"Location verified via PDP: {verify_url}")
                return True
            if attempt == 0:
                await page.wait_for_timeout(3000)
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Location verify failed: {e}")
            else:
                await page.wait_for_timeout(2000)
    logger.warning(f"Location verify: product signal not found at {verify_url}")
    return False


async def establish_location(context, location_cfg, base_url):
    """Establish location on a fresh page in `context`: navigate to base_url,
    FILL if the panel appears, verify via PDP, and return the storage_state
    bundle (cookies + localStorage origins) — the delta carrier (S1.6 m2/m3).

    Returns (verified: bool, bundle: dict|None).
    """
    page = await context.new_page()
    try:
        try:
            await page.goto(base_url, timeout=45000, wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
        except Exception as e:
            logger.warning(f"Location establish: could not load base URL: {e}")
            return False, None

        if await location_panel_visible(page, location_cfg):
            await fill_location(page, location_cfg)
        else:
            logger.info("Location establish: no panel — may already be set via seeded state")

        verified = await verify_location(page, location_cfg)

        bundle = None
        try:
            bundle = await context.storage_state()
        except Exception as e:
            logger.warning(f"Location establish: storage_state capture failed: {e}")

        # Persist the carrier bundle for reuse / provenance
        carrier_file = getattr(location_cfg, "cookies_file", None)
        if bundle and carrier_file:
            try:
                Path(carrier_file).parent.mkdir(parents=True, exist_ok=True)
                Path(carrier_file).write_text(json.dumps(bundle, indent=2))
                logger.info(f"Location carrier bundle saved to {carrier_file}")
            except Exception as e:
                logger.warning(f"Location establish: carrier save failed: {e}")

        return verified, bundle
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def seed_storage_state(context, bundle: dict) -> None:
    """Seed a storage_state bundle (cookies + localStorage origins) into a
    context. Safe to call per incognito context (S1.6 mechanism 2)."""
    if not bundle:
        return
    cookies = bundle.get("cookies") or []
    if cookies:
        try:
            await context.add_cookies(cookies)
        except Exception as e:
            logger.debug(f"seed_storage_state: add_cookies failed: {e}")
    for origin in bundle.get("origins") or []:
        entries = origin.get("localStorage") or []
        if not entries:
            continue
        try:
            ls_json = json.dumps({e["name"]: e["value"] for e in entries})
            await context.add_init_script(
                f"try {{ const _ls = {ls_json}; "
                f"for (const [k, v] of Object.entries(_ls)) {{ localStorage.setItem(k, v); }} "
                f"}} catch(e) {{}}"
            )
        except Exception as e:
            logger.debug(f"seed_storage_state: localStorage seed failed: {e}")


def load_carrier_bundle(location_cfg):
    """Load a previously-saved storage_state carrier bundle, or None.

    Tolerates the legacy cookie-list format (returns it wrapped as a bundle)."""
    carrier_file = getattr(location_cfg, "cookies_file", None)
    if not carrier_file or not Path(carrier_file).exists():
        return None
    try:
        data = json.loads(Path(carrier_file).read_text())
    except Exception:
        return None
    if isinstance(data, dict) and "cookies" in data:
        return data
    if isinstance(data, list):  # legacy cookie-only file
        return {"cookies": data, "origins": []}
    return None
