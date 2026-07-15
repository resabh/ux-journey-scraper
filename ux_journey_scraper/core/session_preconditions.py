"""Shared session-precondition helpers used by BOTH crawl paths.

Standing rule (h027): logic needed by more than one crawl path lands in a
shared module FIRST. This module is the shared seam. Stage 2 (h027) will
consolidate stealth/dismissal here too; for now it holds occlusion
measurement (S1.12 mechanism 4) and location establishment (S1.6).
"""

import logging

logger = logging.getLogger(__name__)

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
