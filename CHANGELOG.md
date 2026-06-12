# Changelog

All notable changes to UX Journey Scraper will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added - Schema contract v2.3, journey coverage, directed flows

- **Schema contract in-repo**: `schemas/journey-schema-v2.2.json` (frozen) and
  `schemas/journey-schema-v2.3.json` (current). `core/schema_validator.py`
  validates every journey dict/file; `Journey.save()` validates on every write;
  CI runs a producer self-test (`scripts/validate_journeys.py --self-test`).
- **v2.3 contract changes** (fixes corpus v1 defects):
  - `screenshot_path` / `html_path` now written relative to journey.json's
    directory (was CWD-relative); absolute paths rejected by schema (defect #3).
  - `page_type` classification fixed for Shopify-like URLs:
    `/products/<handle>` = pdp, `/collections/<handle>` = plp; `info` added to
    the enum (defect #1).
  - Plain-file URLs (`.md`, `.txt`, feeds, assets) are never captured as
    journey steps (defect #4, the `/agents.md` stray capture).
  - Platform viewport/UA/locale now actually applied to the crawlee browser
    context, with the fingerprint constrained to the device class — mobile
    crawls previously rendered desktop layouts (defect #5). Steps record
    `device_pixel_ratio` and `rendered_viewport`; validation asserts
    screenshot pixel width / DPR equals the declared viewport width.
- **Journey coverage report** (`core/coverage_reporter.py`): per-site
  checklist of expected journeys (browse→PDP, add-to-cart→cart-with-items,
  checkout-start, search→results, login/account, wishlist, order tracking,
  policy pages + site-specific extras from the `coverage:` config block).
  Emits `coverage.json` + human-readable `coverage.md` found/missed table
  after every crawl.
- **Directed flows** (`core/flow_runner.py`): the crawler now actively
  COMPLETES add-to-cart → cart-with-items → checkout-start (stopping at
  checkout — no payment, no orders), search→results, and login captures,
  instead of only following links. Runs per platform after the
  link-following sessions; steps are tagged `page_data.flow`.
- **CLI**: `ux-journey validate <paths>` and `ux-journey coverage <run_dir>`.

## [0.5.0] - 2026-03-21

### Added - Native & Wrapper App Testing

**Major Feature**: Native Android and iOS app testing via Appium. All four mobile
app architecture types are now supported: WebView wrappers (Ionic/Capacitor/Cordova),
React Native, Flutter, and pure native (Swift/Kotlin).

#### New Platform Types

- **`native_android`**: Android app testing via UiAutomator2
- **`native_ios`**: iOS app testing via XCUITest (macOS only)

#### New Core Modules

- **`core/appium_session.py`**: Builds Appium capabilities for Android (UiAutomator2)
  and iOS (XCUITest); validates server reachability before crawl starts
- **`core/native_element_detector.py`**: Traverses Appium accessibility tree and
  scores elements using the same `PRIORITY_KEYWORDS` table as `NavigationQueue`
- **`core/gesture_engine.py`**: Touch primitives — tap, scroll, swipe-back (iOS edge
  gesture + Android BACK key), pull-to-refresh, long press, keyboard dismiss
- **`core/mobile_state_registry.py`**: Perceptual-hash (pHash) based screen
  deduplication; falls back to MD5 when `imagehash` is not installed
- **`core/appium_crawler.py`**: Main crawler — same public interface as
  `AutonomousCrawler` (`crawl() → Journey`, `get_stats() → Dict`);
  handles WebView, native, and Flutter (semantics-disabled) modes

#### Config Changes (`config/scrape_config.py`)

- New `NativeAppConfig` dataclass (Appium server, app package/bundle ID, device name,
  platform version, AVD name, APK/IPA path, simulator UDID)
- `PlatformConfig.viewport` is now `Optional` — not required for native platforms
- `PlatformConfig.native: Optional[NativeAppConfig]` — required for native platforms
- `PlatformConfig.is_native` and `PlatformConfig.is_web` boolean properties
- `ScrapeConfig.load()` parses the `native:` YAML block into `NativeAppConfig`

#### CLI Changes (`cli/main.py`)

- Native platforms route to `AppiumCrawler`; web platforms to `AutonomousCrawler`
- Graceful skip with install instructions when Appium package is not installed
- Version bumped to `0.5.0`

#### Platform Discovery (`core/platform_discovery.py`)

- `_discover_ios_bundle_id()`: Live iTunes Search API (public, no key); table fallback
- `_discover_android_package()`: Live `google-play-scraper` search; table fallback

#### Architecture Routing

- WebView (Ionic/Capacitor): switches to WEBVIEW context → DOM automation
- React Native / Swift / Kotlin: native accessibility tree traversal
- Flutter (semantics disabled): screenshot-only scroll mode with log warning
- OS permission dialogs auto-dismissed (camera, location, notifications)

### Added - Dependencies (optional group `[native]`)

- `Appium-Python-Client>=3.1.0`
- `google-play-scraper>=1.2.0`
- `imagehash>=4.3.0`

Install with: `pip install 'ux-journey-scraper[native]'`

### Output Structure

```
journey_output/
  web_desktop/journey.json + screenshots/    ← Phase 1
  web_mobile/journey.json  + screenshots/    ← Phase 1
  native_android/journey.json + screenshots/ ← Phase 2 (new)
  native_ios/journey.json  + screenshots/    ← Phase 2 (new, macOS only)
```

## [0.4.0] - 2026-03-21

### Added - Multi-Platform Web Crawling

**Major Feature**: `ux-journey crawl` now loops over all configured platforms in a single run, producing per-platform output directories.

#### Multi-Platform Crawling

- **All platforms in one run**: Configure `web_desktop`, `web_mobile`, and `web_tablet` in YAML; the crawler runs each sequentially and saves results to separate subdirs (`journey_output/web_desktop/`, `journey_output/web_mobile/`, `journey_output/web_tablet/`)
- **Tablet support**: `web_tablet` (iPad Air 820×1180) is now a first-class platform alongside desktop and mobile
- **Per-platform output**: Each platform gets its own `journey.json` and `screenshots/` folder

#### Bug Fixes

- **Tablet UA string**: Fixed truncated iPad user-agent (was missing `(KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1` suffix)
- **Tablet `device_scale_factor`**: Fixed — tablet now correctly gets `device_scale_factor=2.0` (same as mobile); was incorrectly using `1.0`

### Changed

- **Version**: Bumped to 0.4.0
- **CLI**: `crawl` command header now shows `v0.4.0`; output summary shows per-platform page counts

### Added - Dependencies

- `aiohttp>=3.9.0`: Required by `platform_discovery.py` (was imported but missing from `pyproject.toml`)

### Roadmap - Phase 2 (v0.5.0)

Native iOS/Android app testing via Appium is documented as the next milestone. Stubs (`platform_discovery.py`, `app_architecture_detector.py`) are preserved for Phase 2 integration.

## [0.2.0] - 2026-03-19

### Added - Autonomous Crawling

**Major Feature**: Full autonomous crawling capabilities - the scraper now intelligently navigates websites without manual intervention.

#### New Core Components

- **YAML Configuration System** (`config/scrape_config.py`): Config-first approach replacing individual CLI flags
- **Autonomous Crawler** (`core/autonomous_crawler.py`): Priority queue-based intelligent navigation engine
- **Navigation Queue** (`core/navigation_queue.py`): Priority heap for smart action ordering ("Add to Cart" before "Learn More")
- **Element Intelligence** (`core/element_intelligence.py`): 4-strategy clickable detection (semantic, ARIA, onclick, cursor:pointer)
- **Page Readiness Engine** (`core/page_readiness.py`): Advanced readiness detection (DOM stability, spinners, lazy-load)
- **State Registry** (`core/state_registry.py`): 3-layer deduplication (URL, DOM hash, structural hash)
- **Human Behavior Simulator** (`core/human_behaviour.py`): Bezier mouse movements, realistic typing, human delays
- **Stealth Browser** (`core/stealth_browser.py`): Anti-bot detection with full fingerprint spoofing
- **Auth Manager** (`core/auth_manager.py`): Session injection + login flow automation
- **Form Filler** (`core/form_filler.py`): Smart autocomplete-based form filling with payment safeguards

#### New CLI Commands

- `ux-journey crawl --config <file>`: Primary command for autonomous crawling (v0.2.0)
- Config file support: All settings now via YAML instead of CLI flags

#### Enhanced Capabilities

- **SPA Support**: Full support for single-page applications with dynamic content
- **Lazy Loading**: Scroll-triggered content detection and loading
- **Checkout Flows**: Automatic form filling for e-commerce checkout (test data only)
- **Logged-in Crawling**: Session management and auth wall recovery
- **Anti-Bot Bypass**: Stealth browser with 16-point fingerprint spoofing
- **Priority Navigation**: High-value actions (purchase flows) prioritized over low-value (footer links)

### Changed

- **Version**: Bumped to 0.2.0
- **CLI**: Old commands (`record`, `run`) now deprecated with warnings
- **Output**: Enhanced journey format (same structure, enhanced metadata coming in v0.3.0)

### Added - Dependencies

- `pyyaml>=6.0.0`: YAML configuration parsing
- `aiofiles>=23.0.0`: Async file I/O operations

### Added - Safety Features

- **Payment Submit Safeguard**: Global kill switch prevents real payment submissions
- **Test Cards Only**: Validates only test payment cards are used (4111111111111111, etc.)
- **CAPTCHA Detection**: Detects and flags CAPTCHA challenges

### Added - Documentation

- `MIGRATION.md`: Comprehensive v0.1.0 → v0.2.0 migration guide
- `scrape-config.example.yaml`: Full configuration template with comments
- Enhanced README with autonomous crawling examples

### Improved

- **Page Readiness**:
  - From: Basic `networkidle` check
  - To: DOM stability + spinner dismissal + image loading + lazy-load scroll
  - **Impact**: Fixes "screenshot too early" problem (most critical UX capture issue)

- **Element Detection**:
  - From: Hardcoded `button`, `a` selectors
  - To: 4-strategy detection (semantic, ARIA, event listeners, CSS cursor)
  - **Impact**: Finds 3-5x more interactive elements

- **State Deduplication**:
  - From: URL comparison only
  - To: 3-layer (URL normalization, DOM hash, structural hash)
  - **Impact**: Prevents infinite loops on SPAs

### Breaking Changes

- **Config Required**: `ux-journey crawl` requires `--config` flag
- **Version Change**: CLI version now shows 0.2.0
- **Deprecated Commands**: `record` and `run` still work but show deprecation warnings

### Expected Coverage Improvement

- **v0.1.0 Baseline**: 30% of websites work well (static sites only)
- **v0.2.0 Target**: 80-90% of websites (SPAs, e-commerce, auth-required sites)

### Known Limitations (v0.2.0)

- Integration tests not yet implemented (Task #14, #15 pending)
- Context file output not yet implemented (planned for v0.3.0)
- Multi-platform parallel crawling not yet implemented
- iOS/Android native app support not yet implemented

### Technical Debt

- Unit tests needed for new modules (Tasks #14, #15)
- Performance benchmarking needed
- Memory profiling for long crawls needed

## [0.1.0] - TBD

### Added

- **Journey Recording**: Capture multi-step user journeys through websites using Playwright
- **Screenshot Capture**: Automatically screenshot every step in the journey
- **PII Protection**: Blur personally identifiable information (emails, credit cards, phone numbers) in screenshots
- **UX Analysis**: Apply 324+ research-backed e-commerce UX guidelines to captured journeys
- **Accessibility Validation**: WCAG 2.1 Level A/AA compliance checks
- **Robots.txt Compliance**: Respect website robots.txt files to avoid scraping restricted areas
- **Interactive Reports**: Generate HTML reports with annotated screenshots and findings
- **CLI Tool**: Command-line interface with `ux-journey` command
- **Comprehensive Testing**: Unit tests for all core functionality
- **CI/CD Pipeline**: Automated testing and publishing via GitHub Actions
- **Documentation**: README, CONTRIBUTING, PUBLISHING guides

### Features in Detail

#### Journey Recording

- Playwright-based browser automation
- Multi-step journey capture with navigation tracking
- Automatic screenshot capture at each step
- Journey metadata (timestamps, URLs, page titles)
- Export journey data as JSON

#### UX Guidelines

- 324+ guidelines from e-commerce research
- Categorized by: buttons, forms, navigation, content, checkout, mobile, etc.
- Severity levels: critical, major, minor
- Fix suggestions for each violation
- Guideline references and sources

#### Privacy & Ethics

- PII blurring using pattern matching (emails, credit cards, SSN, phone numbers)
- Robots.txt parsing and compliance
- Rate limiting to avoid overwhelming servers
- User-agent identification
- Respects meta robots tags

#### Accessibility

- WCAG 2.1 Level A/AA checks
- Color contrast validation
- Alt text detection
- Keyboard navigation support checks
- Screen reader compatibility validation
- Focus indicator checks

#### Reports

- HTML reports with embedded screenshots
- Violation summaries with severity breakdown
- Step-by-step journey visualization
- Guideline references and explanations
- Exportable findings as JSON

### Technical Improvements

- Type hints throughout codebase
- Comprehensive error handling
- Logging with configurable verbosity
- Modular architecture for extensibility
- Memory-efficient screenshot handling
- Async/await support for performance

### Documentation

- README with installation and usage examples
- CONTRIBUTING guide for developers
- PUBLISHING guide for maintainers
- API documentation (docstrings)
- Example usage in CLI help

### Testing & Quality

- Unit tests for core functionality
- Integration tests (skipped in CI, manual for now)
- Code coverage tracking
- Linting with flake8
- Code formatting with black
- Import sorting with isort
- Pre-commit hooks

### CI/CD

- GitHub Actions workflow
- Automated testing on Python 3.9, 3.10, 3.11, 3.12
- Linting and formatting checks
- Code coverage reporting to Codecov
- Automated PyPI publishing on release
- Security scanning (dependency, SAST, secrets)

### Known Limitations

- Integration tests require manual setup (Playwright installation)
- ecommerce-ux-guidelines package is optional (full UX analysis requires it)
- Limited to Chromium browser (Firefox, Safari not yet supported)
- English language only for guideline text
- No parallel journey recording (single-threaded)

### Breaking Changes

- None (initial release)

### Deprecated

- None (initial release)

### Security

- PII blurring to prevent accidental exposure of sensitive data
- Robots.txt compliance to respect website policies
- No telemetry or data collection
- Local-only processing

### Fixed

- None (initial release)

---

## Release Notes Template (for future releases)

### [X.Y.Z] - YYYY-MM-DD

#### Added

- New features

#### Changed

- Changes to existing functionality

#### Deprecated

- Soon-to-be removed features

#### Removed

- Removed features

#### Fixed

- Bug fixes

#### Security

- Security improvements and fixes

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 0.1.0   | TBD  | Initial release |

## How to Release

1. Update this CHANGELOG.md with all changes
2. Update version in `pyproject.toml` and `setup.py`
3. Run pre-publish checks: `./scripts/pre-publish-check.sh`
4. Commit: `git commit -am "Bump version to X.Y.Z"`
5. Tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
6. Push: `git push && git push --tags`
7. Create GitHub Release (triggers PyPI publication)

See [PUBLISHING.md](PUBLISHING.md) for detailed instructions.
