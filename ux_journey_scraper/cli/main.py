"""
Command-line interface for UX Journey Scraper.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

import click
from ux_journey_scraper.config.scrape_config import ScrapeConfig
from ux_journey_scraper.core.autonomous_crawler import AutonomousCrawler
from ux_journey_scraper.core.cookie_jar import CookieJar
from ux_journey_scraper.core.journey_recorder import Journey, JourneyRecorder
from ux_journey_scraper.core.profile_manager import ProfileManager

try:
    from ux_journey_scraper.core.app_provisioner import AppProvisioner
    from ux_journey_scraper.core.appium_crawler import AppiumCrawler

    _APPIUM_AVAILABLE = True
except ImportError:
    _APPIUM_AVAILABLE = False

try:
    from ux_journey_scraper.core.crawlee_adapter import CrawleeAdapter, is_crawlee_available

    _CRAWLEE_AVAILABLE = is_crawlee_available()
except ImportError:
    _CRAWLEE_AVAILABLE = False


async def _run_platform_async(scrape_config, platform, platform_dir, engine="auto", browser_type="webkit"):
    """Async crawl execution — single event loop for warm-up + crawl."""
    from pathlib import Path as _Path

    platform_dir = _Path(platform_dir)

    # Auto warm-up if profile is fresh/stale
    pm = ProfileManager()
    if pm.is_fresh():
        click.echo("  Profile is fresh/stale — auto warming up...")
        try:
            await pm.warm_up(browser_type=browser_type)
            click.echo(f"  Warm-up complete: {len(pm.get_cookies())} cookies")
        except Exception as e:
            click.echo(f"  Warm-up failed (continuing without): {e}")

    if platform.is_native:
        if not _APPIUM_AVAILABLE:
            click.echo(f"  Skipping {platform.type}: Appium not installed.")
            click.echo("  Install: pip install 'ux-journey-scraper[native]'")
            return 0
        click.echo(f"Platform: {platform.type} (native app via Appium)")
        crawler = AppiumCrawler(
            config=scrape_config,
            output_dir=str(platform_dir),
            platform=platform,
        )
    elif engine == "crawlee" or (engine == "auto" and _CRAWLEE_AVAILABLE):
        viewport = platform.viewport or {}
        w = viewport.get("width", "?")
        h = viewport.get("height", "?")
        click.echo(f"Platform: {platform.type} ({w}x{h}) [crawlee engine]")
        jar = CookieJar()
        pm.seed_cookie_jar(jar, urlparse(scrape_config.base_url).netloc.replace("www.", ""))
        crawler = CrawleeAdapter(
            config=scrape_config,
            output_dir=str(platform_dir),
            platform=platform,
            browser_type=browser_type,
            cookie_jar=jar,
        )
    else:
        viewport = platform.viewport or {}
        w = viewport.get("width", "?")
        h = viewport.get("height", "?")
        click.echo(f"Platform: {platform.type} ({w}x{h}) [local engine]")
        crawler = AutonomousCrawler(
            config=scrape_config,
            output_dir=str(platform_dir),
            platform=platform,
        )

    journey = await crawler.crawl()

    # Absorb cookies back to profile
    if hasattr(crawler, 'cookie_jar'):
        pm.absorb_cookie_jar(crawler.cookie_jar)

    output_file = platform_dir / "journey.json"
    journey.save(str(output_file))
    pages = crawler.get_stats()["pages_captured"]
    click.echo(f"  {platform.type}: {pages} pages -> {output_file}")
    return pages


def _run_platform(scrape_config, platform, platform_dir, engine="auto", browser_type="webkit"):
    """Sync wrapper — single asyncio.run() for the entire platform crawl."""
    return asyncio.run(_run_platform_async(scrape_config, platform, platform_dir, engine, browser_type))


@click.group()
@click.version_option(version="0.5.0")
def cli():
    """UX Journey Scraper - Autonomous web crawler for capturing user journeys."""
    pass


@cli.command()
@click.option("--config", required=True, help="Path to YAML configuration file")
@click.option(
    "--output-dir", default="journey_output", help="Output directory for results"
)
@click.option(
    "--engine",
    type=click.Choice(["auto", "crawlee", "local"]),
    default="auto",
    help="Crawl engine: auto (crawlee if available), crawlee, or local",
)
@click.option(
    "--browser-type",
    type=click.Choice(["webkit", "chromium", "firefox"]),
    default="webkit",
    help="Browser engine: webkit (Safari, stealthiest), chromium, firefox",
)
def crawl(config, output_dir, engine, browser_type):
    """Autonomous crawl using YAML configuration (v0.5.0)."""
    click.echo(f"\n{'='*60}")
    click.echo(f"  UX JOURNEY AUTONOMOUS CRAWLER v0.5.0")
    click.echo(f"{'='*60}\n")

    try:
        # Load configuration
        click.echo(f"Loading configuration from: {config}")
        scrape_config = ScrapeConfig.load(config)
        click.echo(f"Configuration loaded")
        click.echo(f"   Target: {scrape_config.target['name']}")
        click.echo(f"   Base URL: {scrape_config.target['base_url']}")
        click.echo(f"   Platforms: {len(scrape_config.platforms)}")
        click.echo(f"   Seed URLs: {len(scrape_config.seed_urls)}")
        click.echo(f"   Max pages: {scrape_config.crawler.max_pages}")

        # Run crawl for each platform using shared _run_platform()
        click.echo(f"\nStarting multi-platform crawl...\n")
        total_pages = 0
        for platform in scrape_config.platforms:
            platform_dir = Path(output_dir) / platform.type
            total_pages += _run_platform(scrape_config, platform, platform_dir, engine=engine, browser_type=browser_type)

        click.echo(f"\n{'='*60}")
        click.echo(f"All platforms complete!")
        click.echo(f"Total pages captured: {total_pages}")
        click.echo(f"Output directory: {output_dir}")
        click.echo(f"{'='*60}\n")

    except FileNotFoundError as e:
        click.echo(f"\nConfiguration file not found: {e}")
    except Exception as e:
        click.echo(f"\nCrawl error: {e}")
        import traceback

        traceback.print_exc()


@cli.command()
@click.option("--start-url", required=True, help="Starting URL for the journey")
@click.option("--output", default="journey.json", help="Output file path")
@click.option("--viewport", default="1920x1080", help="Viewport size (e.g., 1920x1080)")
@click.option("--blur-pii/--no-blur-pii", default=True, help="Blur PII in screenshots")
@click.option(
    "--respect-robots/--ignore-robots", default=True, help="Respect robots.txt"
)
@click.option(
    "--headless/--no-headless", default=False, help="Run browser in headless mode"
)
def record(start_url, output, viewport, blur_pii, respect_robots, headless):
    """[DEPRECATED] Record a user journey interactively. Use 'crawl' for v0.2.0 features."""
    click.echo(
        "⚠️  WARNING: This command is deprecated. Use 'ux-journey crawl --config <file>' for v0.2.0 features.\n"
    )
    try:
        # Parse viewport
        width, height = map(int, viewport.split("x"))
    except ValueError:
        click.echo("❌ Invalid viewport format. Use WIDTHxHEIGHT (e.g., 1920x1080)")
        return

    click.echo(f"\n{'='*60}")
    click.echo(f"  UX JOURNEY RECORDER")
    click.echo(f"{'='*60}\n")

    # Create recorder
    recorder = JourneyRecorder(
        start_url=start_url,
        viewport=(width, height),
        blur_pii=blur_pii,
        respect_robots=respect_robots,
        headless=headless,
    )

    # Record journey
    try:
        journey = asyncio.run(recorder.record())

        # Save journey
        journey.save(output)

        click.echo(f"\n{'='*60}")
        click.echo(f"✅ Journey recording complete!")
        click.echo(f"📁 Saved to: {output}")
        click.echo(f"{'='*60}\n")

    except KeyboardInterrupt:
        click.echo("\n\n⚠️  Recording cancelled by user")
    except Exception as e:
        click.echo(f"\n❌ Error during recording: {e}")


@cli.command()
@click.argument("journey_file", type=click.Path(exists=True))
def info(journey_file):
    """Show information about a recorded journey."""
    try:
        journey = Journey.load(journey_file)

        click.echo(f"\n{'='*60}")
        click.echo(f"  JOURNEY INFO")
        click.echo(f"{'='*60}\n")
        click.echo(f"📍 Start URL: {journey.start_url}")
        click.echo(f"📐 Viewport: {journey.viewport[0]}x{journey.viewport[1]}")
        click.echo(f"⏱️  Start Time: {journey.start_time}")
        click.echo(f"⏱️  End Time: {journey.end_time}")
        click.echo(f"📊 Total Steps: {len(journey.steps)}")
        click.echo(f"\nSteps:")
        for step in journey.steps:
            click.echo(f"  {step.step_number}. {step.title} ({step.url})")
        click.echo(f"\n{'='*60}\n")

    except Exception as e:
        click.echo(f"❌ Error loading journey: {e}")


@cli.command()
@click.option(
    "--brand", required=True, help="Brand name to scrape (e.g. 'Amazon', 'Flipkart')"
)
@click.option(
    "--platforms",
    default="web_desktop,web_mobile,native_android,native_ios",
    help="Comma-separated platforms: web_desktop,web_mobile,native_android,native_ios",
)
@click.option(
    "--output-dir", default="journey_output", help="Output directory for results"
)
@click.option("--max-pages", default=10, help="Max pages to capture per platform")
@click.option(
    "--appium-server", default="http://localhost:4723", help="Appium server URL"
)
@click.option(
    "--local/--no-local",
    default=False,
    help="Force local Patchright browser (ignore Browserbase env vars)",
)
@click.option(
    "--engine",
    type=click.Choice(["auto", "crawlee", "local"]),
    default="auto",
    help="Crawl engine: auto (crawlee if available), crawlee, or local",
)
@click.option(
    "--browser-type",
    type=click.Choice(["webkit", "chromium", "firefox"]),
    default="webkit",
    help="Browser engine: webkit (Safari, stealthiest), chromium, firefox",
)
def scrape(brand, platforms, output_dir, max_pages, appium_server, local, engine, browser_type):
    """Auto-provision and scrape a brand across all platforms.

    Example: ux-journey scrape --brand Amazon --platforms web_desktop,web_mobile,native_android
    """
    click.echo(f"\n{'='*60}")
    click.echo(f"  UX JOURNEY BRAND SCRAPER v0.5.0")
    click.echo(f"{'='*60}\n")
    # --local forces local engine
    if local and engine == "auto":
        engine = "local"

    click.echo(f"Brand:     {brand}")
    click.echo(f"Platforms: {platforms}")
    click.echo(f"Engine:    {engine}")
    click.echo(f"Output:    {output_dir}\n")

    from ux_journey_scraper.config.scrape_config import (
        AuthConfig,
        BrowserProvider,
        CrawlerConfig,
        NativeAppConfig,
        PlatformConfig,
        ScrapeConfig,
    )

    # Known brand web URLs — fallback to www.{brand}.com
    BRAND_URLS = {
        "amazon": "https://www.amazon.in",
        "flipkart": "https://www.flipkart.com",
        "nykaa": "https://www.nykaa.com",
        "myntra": "https://www.myntra.com",
        "ajio": "https://www.ajio.com",
        "meesho": "https://www.meesho.com",
        "swiggy": "https://www.swiggy.com",
        "zomato": "https://www.zomato.com",
        "snapdeal": "https://www.snapdeal.com",
        "walmart": "https://www.walmart.com",
        "target": "https://www.target.com",
        "ebay": "https://www.ebay.com",
        "etsy": "https://www.etsy.com",
        "shein": "https://www.shein.com",
        "temu": "https://www.temu.com",
    }
    sanitized = brand.strip().replace(" ", "").lower()
    base_url = BRAND_URLS.get(sanitized, f"https://www.{sanitized}.com")

    VIEWPORTS = {
        "web_desktop": {"width": 1920, "height": 1080},
        "web_mobile": {"width": 390, "height": 844},
        "web_tablet": {"width": 820, "height": 1180},
    }
    platform_list = [p.strip() for p in platforms.split(",")]
    total_pages = 0

    # Build platform list — provision native apps first
    platform_configs = []
    for platform_type in platform_list:
        click.echo(f"{'─'*50}")
        click.echo(f"Setting up: {platform_type}")
        try:
            if platform_type in ("native_android", "native_ios"):
                if not _APPIUM_AVAILABLE:
                    click.echo("  Skipping: Appium not installed.")
                    continue
                provisioner = AppProvisioner()
                native_cfg = asyncio.run(
                    provisioner.provision(brand, platform_type, appium_server)
                )
                platform_configs.append(
                    PlatformConfig(type=platform_type, native=native_cfg)
                )
            elif platform_type in VIEWPORTS:
                platform_configs.append(
                    PlatformConfig(
                        type=platform_type, viewport=VIEWPORTS[platform_type]
                    )
                )
            else:
                click.echo(f"  Unknown platform type: {platform_type}")
        except Exception as e:
            click.echo(f"  Provisioning error: {e}")
            import traceback

            traceback.print_exc()

    if not platform_configs:
        click.echo("No platforms configured. Exiting.")
        return

    # Use Browserbase for web platforms if credentials are available,
    # otherwise fall back to local Patchright stealth browser.
    # --local flag forces local browser regardless of env vars.
    import os as _os

    if local:
        browser_provider = BrowserProvider(
            type="local", use_proxy=False, use_stealth=True
        )
        click.echo("  Using local Patchright stealth browser (--local)")
    else:
        has_browserbase = bool(
            _os.environ.get("BROWSERBASE_API_KEY")
            and _os.environ.get("BROWSERBASE_PROJECT_ID")
        )
        browser_provider = BrowserProvider(
            type="browserbase" if has_browserbase else "local",
            use_proxy=False,
            use_stealth=True,
        )
        if has_browserbase:
            click.echo("  Using Browserbase cloud browser (residential IP)")
        else:
            click.echo("  Using local Patchright stealth browser")

    # Build a full ScrapeConfig using ALL ux-journey-scraper features
    scrape_config = ScrapeConfig(
        target={"name": brand, "base_url": base_url},
        platforms=platform_configs,
        auth=AuthConfig(logged_out=True, logged_in=False),
        seed_urls=[base_url],
        crawler=CrawlerConfig(
            max_pages=max_pages,
            respect_robots=False,
            headless=True,
            timeout_per_page_ms=30000,
        ),
        browser=browser_provider,
    )

    # Run each platform through the same _run_platform() used by 'crawl'
    click.echo(f"\nStarting crawl...\n")
    for platform in scrape_config.platforms:
        platform_dir = Path(output_dir) / platform.type
        click.echo(f"{'─'*50}")
        try:
            total_pages += _run_platform(scrape_config, platform, platform_dir, engine=engine, browser_type=browser_type)
        except Exception as e:
            click.echo(f"  Error: {e}")
            import traceback

            traceback.print_exc()

    click.echo(f"\n{'='*60}")
    click.echo(f"All platforms complete! Total pages: {total_pages}")
    click.echo(f"Output: {output_dir}")
    click.echo(f"{'='*60}\n")


@cli.command("warm-up")
@click.option(
    "--browser-type",
    type=click.Choice(["webkit", "chromium", "firefox"]),
    default="webkit",
    help="Browser engine for warm-up",
)
def warm_up(browser_type):
    """Warm up browser profile by visiting mainstream sites.

    Visits Google, YouTube, Amazon, Wikipedia to accumulate real cookies.
    This makes future scrapes look like a returning user, not a fresh bot.
    Only needs to be run once — cookies persist across scrapes.
    """
    click.echo(f"\n{'='*60}")
    click.echo(f"  PROFILE WARM-UP")
    click.echo(f"{'='*60}\n")

    pm = ProfileManager()

    if not pm.is_fresh():
        click.echo("Profile already exists and is not stale.")
        click.echo(f"Cookies: {len(pm.get_cookies())} total")
        click.echo("Use this command to refresh if needed.\n")

    click.echo("Visiting mainstream sites to build browsing persona...")
    try:
        asyncio.run(pm.warm_up(browser_type=browser_type))
        click.echo(f"\nWarm-up complete! Profile saved.")
        click.echo(f"Cookies: {len(pm.get_cookies())} total across {len(pm._domains)} domains")
    except Exception as e:
        click.echo(f"\nWarm-up failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    cli()
