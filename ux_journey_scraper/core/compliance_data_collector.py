"""
Captures browser-state data for downstream compliance analysis.

This data cannot be derived from HTML alone — it requires a live browser.
The scraper captures it during crawl so compliance-tester can read it
from journey.json without needing a browser.
"""

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


class ComplianceDataCollector:
    """Captures network requests, cookies, localStorage, styles, and tab order."""

    def __init__(self):
        pass

    def attach(self, page):
        """Register network request listener on a per-page bucket.

        Safe to call multiple times on the same page (idempotent).
        The bucket lives on the page object itself so requests from
        different pages never bleed into each other.
        """
        if getattr(page, "_ux_net_bucket", None) is not None:
            return
        bucket: List[Dict] = []
        page._ux_net_bucket = bucket

        def _on_request(request):
            try:
                bucket.append({
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                })
            except Exception:
                pass

        page.on("request", _on_request)

    async def collect(self, page, context) -> dict:
        """Collect all browser-state data for current page.

        Call AFTER page has loaded and settled.

        Returns:
            Dict with network_requests, cookies, local_storage,
            computed_styles, tab_order, focus_styles
        """
        result = {}

        bucket = getattr(page, "_ux_net_bucket", None)
        if bucket is not None:
            result["network_requests"] = list(bucket)
            bucket.clear()
        else:
            result["network_requests"] = []

        # Cookies
        try:
            result["cookies"] = await context.cookies()
        except Exception as e:
            logger.warning(f"Failed to get cookies: {e}")
            result["cookies"] = []

        # localStorage
        try:
            result["local_storage"] = await page.evaluate(
                "() => { try { return Object.entries(localStorage); } catch(e) { return []; } }"
            )
        except Exception as e:
            logger.warning(f"Failed to get localStorage: {e}")
            result["local_storage"] = []

        # Computed styles (for color contrast checks)
        try:
            result["computed_styles"] = await self._get_text_styles(page)
        except Exception as e:
            logger.warning(f"Failed to get computed styles: {e}")
            result["computed_styles"] = []

        # Tab order (for keyboard navigation checks)
        try:
            result["tab_order"] = await self._get_tab_order(page)
        except Exception as e:
            logger.warning(f"Failed to get tab order: {e}")
            result["tab_order"] = []

        # Focus styles (for focus visible checks)
        try:
            result["focus_styles"] = await self._get_focus_styles(page)
        except Exception as e:
            logger.warning(f"Failed to get focus styles: {e}")
            result["focus_styles"] = []

        return result

    async def _get_text_styles(self, page) -> list:
        """Get computed colors for visible text elements."""
        return await page.evaluate("""() => {
            const elements = document.querySelectorAll(
                'p, h1, h2, h3, h4, h5, h6, a, span, li, label, td, th, button'
            );
            const results = [];
            const seen = new Set();
            for (const el of elements) {
                const text = (el.textContent || '').trim();
                if (!text || text.length < 2) continue;
                const styles = window.getComputedStyle(el);
                if (styles.display === 'none' || styles.visibility === 'hidden') continue;
                const key = styles.color + '|' + styles.backgroundColor;
                if (seen.has(key)) continue;
                seen.add(key);
                results.push({
                    tag: el.tagName.toLowerCase(),
                    color: styles.color,
                    background_color: styles.backgroundColor,
                    font_size: styles.fontSize,
                    font_weight: styles.fontWeight,
                    sample_text: text.substring(0, 50)
                });
                if (results.length >= 30) break;
            }
            return results;
        }""")

    async def _get_tab_order(self, page) -> list:
        """Get focusable elements in DOM order with positions."""
        return await page.evaluate("""() => {
            const focusable = document.querySelectorAll(
                'a[href], button, input:not([type="hidden"]), select, textarea, [tabindex]:not([tabindex="-1"])'
            );
            const results = [];
            for (const el of focusable) {
                const styles = window.getComputedStyle(el);
                if (styles.display === 'none' || styles.visibility === 'hidden') continue;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue;
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: (el.textContent || '').trim().substring(0, 50),
                    role: el.getAttribute('role') || '',
                    id: el.id || '',
                    tabindex: el.getAttribute('tabindex') || '',
                    type: el.type || '',
                    top: Math.round(rect.top),
                    left: Math.round(rect.left),
                });
                if (results.length >= 100) break;
            }
            return results;
        }""")

    async def _get_focus_styles(self, page) -> list:
        """Check if focusable elements have visible focus indicators."""
        return await page.evaluate("""() => {
            const focusable = document.querySelectorAll(
                'a[href], button, input:not([type="hidden"]), select, textarea'
            );
            const results = [];
            for (const el of Array.from(focusable).slice(0, 20)) {
                try {
                    const before = window.getComputedStyle(el);
                    const beforeOutline = before.outlineStyle + ' ' + before.outlineWidth + ' ' + before.outlineColor;
                    const beforeBoxShadow = before.boxShadow;
                    el.focus();
                    const after = window.getComputedStyle(el);
                    const afterOutline = after.outlineStyle + ' ' + after.outlineWidth + ' ' + after.outlineColor;
                    const afterBoxShadow = after.boxShadow;
                    results.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim().substring(0, 30),
                        has_visible_focus: afterOutline !== beforeOutline || afterBoxShadow !== beforeBoxShadow,
                        outline_before: beforeOutline,
                        outline_after: afterOutline,
                    });
                    el.blur();
                } catch(e) {}
            }
            return results;
        }""")
