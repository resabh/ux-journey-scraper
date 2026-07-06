# Crawl Gallery — UX Specification (SC-3b)

Authored by BayMAAR command center, 2026-06-14. This spec defines the
user experience of `gallery.html`, the static per-crawl review page.
TODO.md SC-3b is the task; this file is the design contract.

## Purpose & user

One user: a human reviewer (the project owner) deciding whether a crawl
is good enough to promote to the golden corpus. The page must let them
answer three questions in under ~10 minutes, without opening folders:

1. **Did we capture the right things?** (journey/page coverage)
2. **Are the captures honest?** (true platform, fully painted, no
   accidental overlays)
3. **Are the labels right?** (page_type classification, platform tags)

Not a product, not a dashboard, not analysis. One self-contained HTML
file per crawl.

## Hard constraints

- Single `gallery.html` per crawl output directory, inline CSS/JS, zero
  external dependencies (no CDN), works offline via `file://`.
- Image links are RELATIVE paths to the crawl's own screenshots.
- Everything shown is computed from `journey.json` + `coverage.json` at
  generation time — no hardcoded counts, ever.
- Every captured step appears, including failed/flagged ones. Bad
  captures are surfaced, never hidden.
- ZERO Baymard data (public repo). Page types and URLs only.

## Page structure (top to bottom)

### 1. Header bar

Site name · crawl timestamp · schema version · platforms captured
(e.g. `desktop 1920×1080 · mobile 390×844`) · totals: N journeys,
N steps, N flagged. Breakdown chips: count per page_type.

### 2. Coverage strip (the first thing the eye hits)

The expected-journey checklist from `coverage.json`, rendered as
red/green chips: `✓ browse→PDP` `✓ add-to-cart→cart` `✗ checkout-start`
`✗ login`. A missed journey is the single most important fact about a
crawl — it must be visible before any thumbnail.

### 3. View tabs

Three groupings of the same data (client-side toggle, no reload):

- **Journeys** (default) — one horizontal row per session/journey,
  steps in capture order, left to right. This is the Mobbin-style flow
  view: you read a row like a story (home → PLP → PDP → cart).
- **Pairs** — one row per unique URL captured on both platforms:
  desktop capture left, mobile capture right, same page. Rows where one
  platform is missing get an "unpaired" badge. This view is where
  defect #5 (desktop pixels labeled mobile) dies: a real pair is
  visibly different; two identical-looking layouts means the mobile
  capture is fake.
- **Page types** — all captures grouped by page_type (all PDPs
  together, all carts together). This view is where misclassification
  is caught: one glance at the "plp" group shows whether a product page
  is sitting in it.

### 4. Step cards (the unit of everything)

Each capture is a card:

- **Thumbnail**: top-of-page crop at roughly viewport aspect ratio
  (full-page screenshots are extremely tall; the card shows the
  above-the-fold region, the lightbox shows everything). CSS
  `object-fit: cover; object-position: top` on the original file — no
  separate thumbnail generation in v1.
- **Badges**: page_type (color-coded consistently across the page),
  platform (`desktop` / `mobile`), step index within its journey.
- **URL path**, truncated middle, full URL on hover/title.
- **Quality flags**, when present in step metadata (from the SC-2/SC-7
  assertions): `⚠ blank-region`, `⚠ overlay`, `⚠ viewport-mismatch`,
  `⚠ capture-error`. Flagged cards get a visible warning border.

### 5. Filters (sticky, client-side)

Filter the active view by: page_type · platform · journey ·
**flagged only**. Filters combine. A live count shows
"showing X of Y captures" so filtering never silently hides scale.

### 6. Lightbox (click any thumbnail)

- Full-size, full-page screenshot, scrollable.
- Metadata panel: full URL, page title, declared platform+viewport,
  measured screenshot width/DPR (so a mismatch is readable, not just
  flagged), capture timestamp, page_type, relative paths to the
  screenshot and HTML files.
- Prev/next arrows navigate within the current row (journey order in
  Journeys view, the pair partner in Pairs view).
- Esc closes. No zoom/pan machinery needed in v1.

## Why screenshots, not rendered HTML

The scraper saves the rendered DOM snapshot but NOT the site's CSS/JS
asset files, so saved HTML does not re-render faithfully offline.
Screenshots are the visual ground truth; the gallery must never embed
or iframe the captured HTML as if it were the page. The HTML file is
linked from the lightbox metadata for inspection only.

## Out of scope for v1 (do not build)

- Cross-crawl or cross-site browsing, search, tagging, favorites
  (a future BayMAAR-side product, not the scraper's job).
- Any scoring, annotation, or analysis overlay.
- Server, build step, or framework. Static or nothing.
- Pixel-diffing between pairs (human eyes are the v1 diff engine).

## Acceptance walkthrough (how SC-3b is judged)

Generate the gallery for a test crawl, open it from `file://`, and:

1. The coverage strip immediately shows what was found vs missed.
2. In Pairs view, a desktop/mobile pair of the homepage is visibly
   different in layout (or the mismatch is flagged).
3. In Page-types view, spot-check 10 cards — labels match the pixels.
4. Filter to "flagged only" — every known-bad capture in the test crawl
   is present and explained.
5. Total count in the header equals the step count in journey.json.
