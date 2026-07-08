"""
Page analyzer to extract key elements from web pages.
"""

import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

NAV_JS = """() => {
    const result = {primary_nav: [], breadcrumbs: [], footer_nav: []};
    const seen = new Set();

    function addLink(list, el, cap) {
        if (list.length >= cap) return;
        const text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
        const href = el.getAttribute('href') || '';
        if (!text || seen.has(text + '|' + href)) return;
        seen.add(text + '|' + href);
        list.push({text: text.slice(0, 200), href});
    }

    // Primary nav: <nav>, [role="navigation"], header links fallback
    const navEls = document.querySelectorAll('nav, [role="navigation"]');
    const navSet = new Set();
    navEls.forEach(n => navSet.add(n));
    navSet.forEach(n => {
        // Skip footer-embedded navs
        if (n.closest('footer') || n.closest('[role="contentinfo"]') ||
            n.closest('[class*="footer" i]')) return;
        n.querySelectorAll('a[href]').forEach(a => addLink(result.primary_nav, a, 80));
    });

    // Fallback: if no <nav>/[role=navigation], grab header links
    if (result.primary_nav.length === 0) {
        const header = document.querySelector('header, [role="banner"], [class*="header" i]');
        if (header) {
            header.querySelectorAll('a[href]').forEach(a => addLink(result.primary_nav, a, 50));
        }
    }

    // Breadcrumbs
    const bcSels = [
        '[class*="breadcrumb" i]',
        '[aria-label*="breadcrumb" i]',
        'ol.breadcrumb',
        'nav[aria-label*="breadcrumb" i]',
    ];
    for (const sel of bcSels) {
        const el = document.querySelector(sel);
        if (el) {
            el.querySelectorAll('a[href]').forEach(a => addLink(result.breadcrumbs, a, 20));
            // Also grab non-link breadcrumb items (current page)
            if (result.breadcrumbs.length === 0) {
                el.querySelectorAll('li, span').forEach(s => {
                    const t = (s.textContent || '').trim();
                    if (t && result.breadcrumbs.length < 20) {
                        result.breadcrumbs.push({text: t.slice(0, 200), href: ''});
                    }
                });
            }
            break;
        }
    }

    // Footer nav
    seen.clear();
    const footerSels = ['footer', '[role="contentinfo"]', '[class*="footer" i]'];
    for (const sel of footerSels) {
        const el = document.querySelector(sel);
        if (el) {
            el.querySelectorAll('a[href]').forEach(a => addLink(result.footer_nav, a, 30));
            break;
        }
    }

    return result;
}"""

FORMS_JS = """() => {
    const results = [];
    const seen = new Set();

    function extractFields(container) {
        const fields = [];
        container.querySelectorAll('input, select, textarea').forEach(el => {
            if (el.type === 'hidden') return;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return;

            let label = '';
            const id = el.id || '';
            if (id) {
                const lbl = document.querySelector('label[for="' + CSS.escape(id) + '"]');
                if (lbl) label = lbl.textContent.trim();
            }
            if (!label) {
                const parent = el.closest('label');
                if (parent) label = parent.textContent.trim();
            }
            if (!label) label = el.getAttribute('aria-label') || '';

            fields.push({
                type: el.type || el.tagName.toLowerCase(),
                name: el.name || '',
                id: id,
                placeholder: el.placeholder || '',
                required: el.required || false,
                label: label.slice(0, 200),
            });
        });
        return fields;
    }

    function classifyForm(fields, action) {
        const hasSearch = fields.some(f =>
            f.type === 'search' || f.name === 'q' || f.name === 'query' ||
            (f.placeholder && /search/i.test(f.placeholder)));
        if (hasSearch) return 'search';
        const hasPassword = fields.some(f => f.type === 'password');
        if (hasPassword) return 'login';
        return 'other';
    }

    // Real <form> elements
    document.querySelectorAll('form').forEach(form => {
        const fields = extractFields(form);
        if (fields.length === 0) return;
        const action = form.getAttribute('action') || '';
        const key = action + '|' + fields.map(f => f.name).join(',');
        if (seen.has(key)) return;
        seen.add(key);
        results.push({
            action: action,
            method: (form.method || 'get').toUpperCase(),
            fields: fields,
            purpose: classifyForm(fields, action),
        });
    });

    // Virtual forms: visible inputs NOT inside any <form>
    const orphans = document.querySelectorAll(
        'input:not(form input):not([type="hidden"]), ' +
        'select:not(form select), ' +
        'textarea:not(form textarea)'
    );
    const orphanFields = [];
    orphans.forEach(el => {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return;

        let label = '';
        const id = el.id || '';
        if (id) {
            const lbl = document.querySelector('label[for="' + CSS.escape(id) + '"]');
            if (lbl) label = lbl.textContent.trim();
        }
        if (!label) {
            const parent = el.closest('label');
            if (parent) label = parent.textContent.trim();
        }
        if (!label) label = el.getAttribute('aria-label') || '';

        orphanFields.push({
            type: el.type || el.tagName.toLowerCase(),
            name: el.name || '',
            id: id,
            placeholder: el.placeholder || '',
            required: el.required || false,
            label: label.slice(0, 200),
        });
    });

    if (orphanFields.length > 0) {
        results.push({
            action: '',
            method: '',
            fields: orphanFields,
            purpose: classifyForm(orphanFields, ''),
        });
    }

    return results;
}"""

SEARCH_JS = """() => {
    const inputs = [];
    const sels = [
        'input[type="search"]',
        'input[name="q"]',
        'input[name="query"]',
        '[role="searchbox"]',
        '[role="search"] input',
        'input[class*="search" i]',
        'input[placeholder*="search" i]',
    ];
    const seen = new Set();
    for (const sel of sels) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (seen.has(el)) return;
                seen.add(el);
                const rect = el.getBoundingClientRect();
                inputs.push({
                    placeholder: el.placeholder || '',
                    name: el.name || '',
                    type: el.type || '',
                    visible: rect.width > 0 && rect.height > 0,
                });
            });
        } catch(e) {}
    }
    return {has_search_bar: inputs.some(i => i.visible), search_inputs: inputs};
}"""


CLASSIFICATION_SIGNALS_JS = """() => {
    const signals = {};

    // Add-to-cart presence
    const atcTexts = /add to (cart|bag|basket)/i;
    const atcEls = document.querySelectorAll('button, a, input[type="submit"]');
    signals.has_add_to_cart = [...atcEls].some(el => {
        const t = (el.textContent || '').trim();
        return atcTexts.test(t) && el.offsetWidth > 0;
    });

    // Product schema count (JSON-LD)
    let productSchemaCount = 0;
    document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
        try {
            const d = JSON.parse(s.textContent);
            const items = Array.isArray(d) ? d : [d];
            items.forEach(item => {
                if (item['@type'] === 'Product' || item['@type'] === 'ProductGroup') {
                    productSchemaCount++;
                }
                if (item['@graph']) {
                    item['@graph'].forEach(g => {
                        if (g['@type'] === 'Product') productSchemaCount++;
                    });
                }
            });
        } catch(e) {}
    });
    signals.product_schema_count = productSchemaCount;

    // Visible product cards (elements with price indicator + image)
    let cardCount = 0;
    const pricePattern = /[\\u20B9$€£]\\s*[\\d,.]+|Rs\\.?\\s*[\\d,.]+|MRP/;
    for (const el of document.querySelectorAll('div, li, article, a')) {
        const r = el.getBoundingClientRect();
        if (r.width < 100 || r.height < 100 || r.top < -100 || r.top > 3000) continue;
        if (r.width > window.innerWidth * 0.9) continue; // full-width = not a card
        const text = el.innerText || '';
        if (text.length > 500) continue; // too much text for a card
        if (pricePattern.test(text) && el.querySelector('img')) {
            cardCount++;
        }
    }
    signals.visible_product_cards = Math.min(cardCount, 100);

    // Filter/facet controls
    signals.has_filters = !!(
        document.querySelector('[class*="filter" i]:not([class*="filtered" i])') ||
        document.querySelector('[class*="facet" i]') ||
        document.querySelector('[aria-label*="filter" i]') ||
        document.querySelector('[data-testid*="filter" i]')
    );

    // Search results indicators
    const bodyText = (document.body?.innerText || '').slice(0, 3000);
    signals.has_search_results_text = /showing\\s+\\d+|\\d+\\s+results?\\s+for|search results/i.test(bodyText);

    // Breadcrumb depth
    let maxCrumbs = 0;
    const bcSels = ['[class*="breadcrumb" i]', '[aria-label*="breadcrumb" i]', 'ol.breadcrumb'];
    for (const sel of bcSels) {
        const el = document.querySelector(sel);
        if (el) {
            const items = el.querySelectorAll('a, li, span');
            maxCrumbs = Math.max(maxCrumbs, items.length);
            break;
        }
    }
    signals.breadcrumb_depth = maxCrumbs;

    // Single product hero image (large image >30% viewport)
    const imgs = document.querySelectorAll('img');
    signals.has_product_hero = [...imgs].some(img => {
        const r = img.getBoundingClientRect();
        return r.width > window.innerWidth * 0.3 && r.height > 200 && r.top < 800;
    });

    return signals;
}"""


class PageAnalyzer:
    """Extract and analyze page elements (forms, CTAs, navigation, etc.)."""

    async def analyze_page(self, page):
        """
        Analyze a page and extract key elements.

        Args:
            page: Playwright page object

        Returns:
            dict: Page analysis data
        """
        html = await page.content()
        url = page.url
        title = await page.title()

        soup = BeautifulSoup(html, "html.parser")

        navigation = await self._extract_navigation(page)
        forms = await self._extract_forms(page)
        search = await self._extract_search(page)
        ctas = await self._extract_ctas(page)
        buttons = await self._extract_buttons(page)
        links = self._extract_links(soup)
        classification_signals = await self._extract_classification_signals(page)

        return {
            "url": url,
            "title": title,
            "html": html,
            "forms": forms,
            "ctas": ctas,
            "navigation": navigation,
            "buttons": buttons,
            "links": links,
            "search": search,
            "meta": self._extract_meta(soup),
            "classification_signals": classification_signals,
        }

    async def _extract_navigation(self, page):
        """Extract navigation elements via live DOM queries."""
        try:
            return await page.evaluate(NAV_JS)
        except Exception as e:
            logger.warning(f"Navigation extraction failed: {e}")
            return {"primary_nav": [], "breadcrumbs": [], "footer_nav": []}

    async def _extract_forms(self, page):
        """Extract form elements including virtual forms (inputs outside <form> tags)."""
        try:
            return await page.evaluate(FORMS_JS)
        except Exception as e:
            logger.warning(f"Forms extraction failed: {e}")
            return []

    async def _extract_search(self, page):
        """Extract search bar presence and input details."""
        try:
            return await page.evaluate(SEARCH_JS)
        except Exception as e:
            logger.warning(f"Search extraction failed: {e}")
            return {"has_search_bar": False, "search_inputs": []}

    async def _extract_classification_signals(self, page):
        """Extract DOM signals for page type classification."""
        try:
            return await page.evaluate(CLASSIFICATION_SIGNALS_JS)
        except Exception as e:
            logger.warning(f"Classification signals extraction failed: {e}")
            return {}

    async def _extract_ctas(self, page):
        """Extract Call-To-Action elements."""
        ctas = []

        cta_texts = [
            "buy now",
            "add to cart",
            "checkout",
            "purchase",
            "sign up",
            "register",
            "get started",
            "try free",
            "subscribe",
            "download",
            "learn more",
            "shop now",
        ]

        for text in cta_texts:
            elements = await page.query_selector_all(
                f'button:has-text("{text}"), a:has-text("{text}"), '
                f'input[type="submit"][value*="{text}"]'
            )

            for elem in elements:
                try:
                    bbox = await elem.bounding_box()
                    cta_data = {
                        "text": (
                            await elem.inner_text() if await elem.inner_text() else text
                        ),
                        "type": await elem.evaluate("(el) => el.tagName.toLowerCase()"),
                        "position": bbox if bbox else None,
                        "href": (
                            await elem.get_attribute("href")
                            if await elem.evaluate("(el) => el.tagName") == "A"
                            else None
                        ),
                    }
                    ctas.append(cta_data)
                except:
                    continue

        return ctas

    async def _extract_buttons(self, page):
        """Extract button elements with position and size."""
        buttons = []

        button_elements = await page.query_selector_all(
            'button, input[type="button"], input[type="submit"]'
        )

        for button in button_elements[:50]:
            try:
                text = await button.inner_text()
                bbox = await button.bounding_box()

                if bbox:
                    button_data = {
                        "text": text,
                        "position": {
                            "x": int(bbox["x"]),
                            "y": int(bbox["y"]),
                            "width": int(bbox["width"]),
                            "height": int(bbox["height"]),
                        },
                        "type": await button.get_attribute("type") or "button",
                        "disabled": await button.is_disabled(),
                    }
                    buttons.append(button_data)
            except:
                continue

        return buttons

    def _extract_links(self, soup):
        """Extract links from the page."""
        links = []

        for link in soup.find_all("a", href=True)[:100]:
            links.append({"text": link.get_text(strip=True), "href": link["href"]})

        return links

    def _extract_meta(self, soup):
        """Extract meta information."""
        meta = {
            "description": "",
            "keywords": "",
            "viewport": "",
            "og_title": "",
            "og_description": "",
        }

        desc = soup.find("meta", {"name": "description"})
        if desc:
            meta["description"] = desc.get("content", "")

        keywords = soup.find("meta", {"name": "keywords"})
        if keywords:
            meta["keywords"] = keywords.get("content", "")

        viewport = soup.find("meta", {"name": "viewport"})
        if viewport:
            meta["viewport"] = viewport.get("content", "")

        og_title = soup.find("meta", {"property": "og:title"})
        if og_title:
            meta["og_title"] = og_title.get("content", "")

        og_desc = soup.find("meta", {"property": "og:description"})
        if og_desc:
            meta["og_description"] = og_desc.get("content", "")

        return meta
