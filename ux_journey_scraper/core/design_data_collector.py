"""
Captures design-system data for downstream design-system-builder analysis.

This data requires a live browser (computed styles, CSS variables, component
tree, asset URLs). The scraper captures it during crawl so design-system-builder
can read from journey.json without needing its own browser.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)


class DesignDataCollector:
    """Captures CSS variables, computed styles, component tree, and asset URLs."""

    # --- JS snippets stored as class attributes (same pattern as ComplianceDataCollector) ---

    CSS_VARS_JS = """() => {
        try {
            const styles = getComputedStyle(document.documentElement);
            const vars = {};
            for (let i = 0; i < styles.length; i++) {
                const prop = styles[i];
                if (prop.startsWith('--')) {
                    vars[prop] = styles.getPropertyValue(prop).trim();
                }
            }
            return vars;
        } catch(e) {
            return {};
        }
    }"""

    ALL_STYLES_JS = """() => {
        try {
            const seen = new Set();
            const results = [];
            const els = document.querySelectorAll('*');
            const limit = 200;
            let count = 0;

            for (let i = 0; i < els.length && count < limit; i++) {
                const el = els[i];
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue;

                const tag = el.tagName.toLowerCase();
                const cn = typeof el.className === 'string' ? el.className.trim() : '';
                const selector = cn ? tag + '.' + cn.split(/\\s+/).join('.') : tag;

                if (seen.has(selector)) continue;
                seen.add(selector);
                count++;

                const cs = getComputedStyle(el);
                results.push({
                    selector: selector,
                    styles: {
                        color: cs.color,
                        backgroundColor: cs.backgroundColor,
                        fontFamily: cs.fontFamily,
                        fontSize: cs.fontSize,
                        fontWeight: cs.fontWeight,
                        lineHeight: cs.lineHeight,
                        letterSpacing: cs.letterSpacing,
                        padding: cs.padding,
                        margin: cs.margin,
                        borderRadius: cs.borderRadius,
                        boxShadow: cs.boxShadow,
                        display: cs.display,
                        position: cs.position,
                    }
                });
            }
            return results;
        } catch(e) {
            return [];
        }
    }"""

    COMPONENT_TREE_JS = """() => {
        try {
            const components = [];
            const selectors = {
                header: 'header, [role="banner"]',
                footer: 'footer, [role="contentinfo"]',
                nav: 'nav, [role="navigation"]',
                form: 'form',
                button: 'button, [role="button"], input[type="submit"]',
                card: '[class*="card"], [class*="Card"]',
                modal: '[role="dialog"], [class*="modal"], [class*="Modal"]',
                search: '[role="search"], input[type="search"], [class*="search"]',
                hero: '[class*="hero"], [class*="Hero"], [class*="banner"]',
                breadcrumb: '[class*="breadcrumb"], [role="navigation"][aria-label*="breadcrumb"]',
            };

            for (const [type, sel] of Object.entries(selectors)) {
                const els = document.querySelectorAll(sel);
                els.forEach(el => {
                    const tag = el.tagName.toLowerCase();
                    const cn = typeof el.className === 'string' ? el.className.trim() : '';
                    const id = el.id || '';
                    components.push({
                        type: type,
                        tag: tag,
                        className: cn,
                        id: id,
                        childCount: el.children.length,
                    });
                });
            }
            return components;
        } catch(e) {
            return [];
        }
    }"""

    TEXT_ELEMENTS_JS = """() => {
        try {
            const results = [];
            const CAP = 500;
            const TEXT_TAGS = new Set([
                'p','h1','h2','h3','h4','h5','h6','span','a','li','td','th',
                'label','button','figcaption','blockquote','cite','em','strong',
                'small','sub','sup','dt','dd','summary','legend','caption',
            ]);
            const scrollX = window.scrollX || window.pageXOffset || 0;
            const scrollY = window.scrollY || window.pageYOffset || 0;

            const els = document.querySelectorAll(Array.from(TEXT_TAGS).join(','));
            for (let i = 0; i < els.length && results.length < CAP; i++) {
                const el = els[i];
                const text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
                if (!text || text.length > 500) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue;
                const cs = getComputedStyle(el);
                results.push({
                    tag: el.tagName.toLowerCase(),
                    text: text.slice(0, 200),
                    rect: {
                        x: Math.round(rect.x * 100) / 100,
                        y: Math.round(rect.y * 100) / 100,
                        width: Math.round(rect.width * 100) / 100,
                        height: Math.round(rect.height * 100) / 100,
                    },
                    font_size: cs.fontSize,
                    font_weight: cs.fontWeight,
                    color: cs.color,
                });
            }
            return {
                elements: results,
                coordinate_frame: 'viewport',
                scroll_offset: { x: scrollX, y: scrollY },
                viewport: {
                    width: window.innerWidth,
                    height: window.innerHeight,
                },
                page_size: {
                    width: document.documentElement.scrollWidth,
                    height: document.documentElement.scrollHeight,
                },
            };
        } catch(e) {
            return { elements: [], coordinate_frame: 'viewport', scroll_offset: {x:0,y:0} };
        }
    }"""

    ASSET_URLS_JS = """() => {
        try {
            const assets = { images: [], stylesheets: [], fonts: [] };

            document.querySelectorAll('img[src]').forEach(el => {
                assets.images.push(el.src);
            });
            document.querySelectorAll('source[srcset]').forEach(el => {
                assets.images.push(el.srcset.split(',')[0].trim().split(' ')[0]);
            });

            document.querySelectorAll('link[rel="stylesheet"][href]').forEach(el => {
                assets.stylesheets.push(el.href);
            });

            // Font sources: preload, preconnect, and Google Fonts links
            document.querySelectorAll('link[rel="preload"][as="font"][href]').forEach(el => {
                assets.fonts.push(el.href);
            });
            document.querySelectorAll('link[href*="fonts.googleapis.com"], link[href*="fonts.gstatic.com"]').forEach(el => {
                assets.fonts.push(el.href);
            });

            // Extract @font-face URLs from loaded stylesheets
            try {
                for (const sheet of document.styleSheets) {
                    try {
                        for (const rule of sheet.cssRules) {
                            if (rule instanceof CSSFontFaceRule) {
                                const src = rule.style.getPropertyValue('src');
                                const family = rule.style.getPropertyValue('font-family').replace(/['"]/g, '');
                                const urlMatches = src.match(/url\\(["']?([^"')]+)["']?\\)/g) || [];
                                for (const m of urlMatches) {
                                    const url = m.replace(/url\\(["']?/, '').replace(/["']?\\)/, '');
                                    assets.fonts.push(url);
                                }
                                if (family && !assets._fontFamilies) assets._fontFamilies = [];
                                if (family) assets._fontFamilies.push(family);
                            }
                        }
                    } catch(e) { /* CORS: skip cross-origin sheets */ }
                }
            } catch(e) {}

            // Deduplicate
            assets.images = [...new Set(assets.images)];
            assets.stylesheets = [...new Set(assets.stylesheets)];
            assets.fonts = [...new Set(assets.fonts)];
            if (assets._fontFamilies) assets._fontFamilies = [...new Set(assets._fontFamilies)];

            return assets;
        } catch(e) {
            return { images: [], stylesheets: [], fonts: [] };
        }
    }"""

    async def collect(self, page) -> Dict:
        """Collect all design-system data for current page.

        Call AFTER page has loaded and settled.

        Args:
            page: Playwright page object.

        Returns:
            Dict with css_variables, all_styles, component_tree, asset_urls.
        """
        result = {}

        # CSS custom properties
        try:
            result["css_variables"] = await page.evaluate(self.CSS_VARS_JS)
        except Exception as e:
            logger.warning(f"Failed to get CSS variables: {e}")
            result["css_variables"] = {}

        # Computed styles for visible elements (deduped, up to 200)
        try:
            result["all_styles"] = await page.evaluate(self.ALL_STYLES_JS)
        except Exception as e:
            logger.warning(f"Failed to get computed styles: {e}")
            result["all_styles"] = []

        # Semantic component tree
        try:
            result["component_tree"] = await page.evaluate(self.COMPONENT_TREE_JS)
        except Exception as e:
            logger.warning(f"Failed to get component tree: {e}")
            result["component_tree"] = []

        # Text elements with bounding rects
        try:
            result["text_elements"] = await page.evaluate(self.TEXT_ELEMENTS_JS)
        except Exception as e:
            logger.warning(f"Failed to get text elements: {e}")
            result["text_elements"] = {"elements": [], "coordinate_frame": "viewport", "scroll_offset": {"x": 0, "y": 0}}

        # Asset URLs (images, stylesheets, fonts)
        try:
            result["asset_urls"] = await page.evaluate(self.ASSET_URLS_JS)
        except Exception as e:
            logger.warning(f"Failed to get asset URLs: {e}")
            result["asset_urls"] = {"images": [], "stylesheets": [], "fonts": []}

        return result


async def collect_and_merge_design_data(page, page_data: Dict) -> None:
    """Collect design data and merge into page_data in-place.

    Single definition, called from both crawlee_adapter and flow_runner.
    """
    collector = DesignDataCollector()
    try:
        design_data = await collector.collect(page)
    except Exception as e:
        logger.warning(f"Design data collection failed: {e}")
        return

    page_data["css_variables"] = design_data.get("css_variables", {})
    page_data["component_tree"] = design_data.get("component_tree", [])
    page_data["asset_urls"] = design_data.get("asset_urls", {})
    page_data["text_elements"] = design_data.get("text_elements", {"elements": [], "coordinate_frame": "viewport", "scroll_offset": {"x": 0, "y": 0}})

    if isinstance(page_data.get("computed_styles"), list):
        page_data["computed_styles"] = {"text_elements": page_data["computed_styles"]}
    elif not isinstance(page_data.get("computed_styles"), dict):
        page_data["computed_styles"] = {}
    page_data["computed_styles"]["all_elements"] = design_data.get("all_styles", [])
