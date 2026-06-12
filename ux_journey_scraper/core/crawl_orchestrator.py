"""
Crawl orchestrator - coordinates session-split or continuous crawls.

Handles:
- Session planning and execution
- Cookie persistence across sessions
- Proxy rotation
- Checkpoint/resume capability
- Final context assembly
"""

import asyncio
import json
import logging
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..config.scrape_config import ScrapeConfig
from .autonomous_crawler import AutonomousCrawler
from .cookie_jar import CookieJar
from .coverage_reporter import CoverageReporter
from .flow_runner import FlowRunner
from .page_classifier import PageClassifier
from .profile_manager import ProfileManager
from .proxy_rotator import ProxyRotator
from .session_planner import SessionPlanner, VisitPlan

try:
    from .crawlee_adapter import CrawleeAdapter, is_crawlee_available

    _CRAWLEE_AVAILABLE = is_crawlee_available()
except ImportError:
    _CRAWLEE_AVAILABLE = False

logger = logging.getLogger(__name__)


class CrawlOrchestrator:
    """
    Orchestrates full crawl across multiple platforms and auth states.
    Supports both session-split and continuous modes.
    """

    def __init__(
        self,
        config: ScrapeConfig,
        profile_manager: Optional[ProfileManager] = None,
        browser_type: str = "webkit",
        engine: str = "auto",
        auto_warmup: bool = True,
    ):
        """
        Initialize orchestrator.

        Args:
            config: Full scrape configuration
            profile_manager: Optional ProfileManager for cookie persistence across scrapes
            browser_type: Browser engine — webkit (stealthiest), chromium, firefox
            engine: Crawl engine — auto (crawlee if available), crawlee, local
            auto_warmup: Auto warm-up profile if fresh/stale
        """
        self.config = config
        self.profile_manager = profile_manager or ProfileManager()
        self.browser_type = browser_type
        self.engine = engine
        self.auto_warmup = auto_warmup

    async def run_all(self) -> Dict:
        """
        Main entry point.
        Auto warms up profile if needed, then routes to split or continuous mode.

        Returns:
            Dictionary with crawl results and metadata
        """
        if self.auto_warmup and self.profile_manager.is_fresh():
            logger.info("Profile is fresh/stale — auto warming up...")
            try:
                # Use first platform's UA for warm-up to avoid cookie/UA mismatch
                warmup_ua = None
                if self.config.platforms:
                    warmup_ua = self.config.platforms[0].user_agent
                await self.profile_manager.warm_up(
                    browser_type=self.browser_type, user_agent=warmup_ua
                )
                logger.info(f"Warm-up complete: {len(self.profile_manager.get_cookies())} cookies")
            except Exception as e:
                logger.warning(f"Warm-up failed (continuing without): {e}")

        if self.config.session_strategy.mode == "split":
            return await self._run_split()
        else:
            return await self._run_continuous()

    async def _run_split(self) -> Dict:
        """
        Session-split crawl: multiple visit sessions with cooldowns.
        Each session looks like a real user visit.

        Returns:
            Crawl results dictionary
        """
        run_id = self.config.run_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.config.output_dir) / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Plan all sessions
        planner = SessionPlanner()
        visit_plans = planner.plan(self.config)
        logger.info(f"Session planner created {len(visit_plans)} visit sessions")

        # Estimate time
        total_time = planner.estimate_total_time(visit_plans, self.config)
        logger.info(
            f"Estimated crawl time: {total_time // 60} minutes "
            f"({planner.estimate_total_pages(visit_plans)} pages)"
        )

        # Setup
        domain = urlparse(self.config.base_url).netloc
        cookie_jar = CookieJar(persist_path=output_dir / "cookie_jar.json")
        proxy_rotator = ProxyRotator(self.config.proxy)
        all_screens = []
        checkpoint_path = output_dir / "checkpoint.json"

        # Resume support: skip completed sessions
        completed_session_ids = set()
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text())
            completed_session_ids = set(checkpoint.get("completed_sessions", []))
            cookie_jar.load_from_disk()
            logger.info(f"Resuming: {len(completed_session_ids)} sessions already done")

        # Seed cookie jar from profile on first run
        self.profile_manager.seed_cookie_jar(cookie_jar, domain)

        for i, plan in enumerate(visit_plans):
            # Skip already-completed sessions (resume)
            if plan.session_id in completed_session_ids:
                logger.info(f"Skipping completed session: {plan.session_id}")
                continue

            logger.info(
                f"[{i+1}/{len(visit_plans)}] {plan.session_id}: "
                f"{plan.goal} on {plan.platform.type} ({plan.auth_state})"
            )

            # Cooldown between sessions
            if i > 0 and plan.session_id not in completed_session_ids:
                cooldown = random.randint(
                    self.config.session_strategy.min_cooldown_sec,
                    self.config.session_strategy.max_cooldown_sec,
                )
                logger.info(f"Cooling down {cooldown}s before next session...")
                await asyncio.sleep(cooldown)

            # Get proxy for this session
            proxy_config = proxy_rotator.get_for_slot(plan.proxy_slot)

            # Run visit session — use CrawleeAdapter if available
            if self._use_crawlee():
                crawler = CrawleeAdapter(
                    config=self.config,
                    output_dir=str(output_dir / plan.session_id),
                    platform=plan.platform,
                    browser_type=self.browser_type,
                    cookie_jar=cookie_jar,
                    visit_plan=plan,
                )
            else:
                crawler = AutonomousCrawler(
                    config=self.config,
                    visit_plan=plan,
                    cookie_jar=cookie_jar,
                    proxy_override=proxy_config,
                    output_dir=str(output_dir),
                )

            try:
                journey = await crawler.crawl()

                # Save journey.json (consumers like ux_tester need this)
                session_dir = output_dir / plan.session_id
                session_dir.mkdir(parents=True, exist_ok=True)
                journey_file = session_dir / "journey.json"
                journey.save(str(journey_file))

                screens = self._extract_screens_from_journey(journey)
                all_screens.extend(screens)
                logger.info(f"  → {len(screens)} screens captured")

                # CrawleeAdapter updates cookie_jar internally during crawl.
                # AutonomousCrawler needs explicit cookie harvest.
                if not self._use_crawlee():
                    cookies = await crawler.get_cookies()
                    if cookies:
                        cookie_jar.update(domain, cookies)

                # Checkpoint
                completed_session_ids.add(plan.session_id)
                self._save_checkpoint(
                    checkpoint_path, completed_session_ids, all_screens, plan.proxy_slot
                )

            except Exception as e:
                logger.error(f"Session {plan.session_id} failed: {e}", exc_info=True)
                # Extended cooldown after failure
                await asyncio.sleep(self.config.session_strategy.max_cooldown_sec * 2)

        # Directed flows: actively complete add-to-cart → cart → checkout-start,
        # search → results, login — journeys link-following never reaches.
        await self._run_directed_flows(
            output_dir, cookie_jar, completed_session_ids, checkpoint_path, all_screens
        )

        # Absorb crawl cookies back into profile
        self.profile_manager.absorb_cookie_jar(cookie_jar)

        # Build final context
        context = self._build_context(all_screens, run_id, output_dir)

        # Journey coverage report (found/missed is THE progress metric)
        context["coverage"] = self._emit_coverage(output_dir)

        logger.info(f"Crawl complete: {len(all_screens)} total screens")
        return context

    async def _run_continuous(self) -> Dict:
        """
        Original single-session behavior.
        Used for low-defense sites where splitting is unnecessary.

        Returns:
            Crawl results dictionary
        """
        run_id = self.config.run_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_dir = Path(self.config.output_dir) / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        all_screens = []
        domain = urlparse(self.config.base_url).netloc
        cookie_jar = CookieJar(persist_path=output_dir / "cookie_jar.json")
        self.profile_manager.seed_cookie_jar(cookie_jar, domain)

        journeys = {}
        for platform in self.config.platforms:
            for auth_state in self._auth_states():
                logger.info(f"Crawling: {platform.type} / {auth_state}")
                platform_dir = output_dir / platform.type

                if self._use_crawlee():
                    crawler = CrawleeAdapter(
                        config=self.config,
                        output_dir=str(platform_dir),
                        platform=platform,
                        browser_type=self.browser_type,
                        cookie_jar=cookie_jar,
                    )
                else:
                    crawler = AutonomousCrawler(
                        config=self.config,
                        platform=platform,
                        auth_state=auth_state,
                        output_dir=str(platform_dir),
                    )

                try:
                    journey = await crawler.crawl()

                    # Save journey.json first (consumers need this)
                    platform_dir.mkdir(parents=True, exist_ok=True)
                    journey_file = platform_dir / "journey.json"
                    journey.save(str(journey_file))
                    journeys[platform.type] = journey

                    screens = self._extract_screens_from_journey(journey)
                    all_screens.extend(screens)
                    pages = crawler.get_stats().get("pages_captured", len(screens))
                    logger.info(f"  → {pages} pages -> {journey_file}")

                except Exception as e:
                    logger.error(f"Crawler failed: {e}", exc_info=True)

        await self._run_directed_flows(output_dir, cookie_jar, set(), None, all_screens)

        self.profile_manager.absorb_cookie_jar(cookie_jar)

        context = self._build_context(all_screens, run_id, output_dir)
        context["journeys"] = {k: v for k, v in journeys.items()}
        context["coverage"] = self._emit_coverage(output_dir)
        logger.info(f"Crawl complete: {len(all_screens)} total screens")
        return context

    async def _run_directed_flows(
        self, output_dir: Path, cookie_jar, completed_session_ids, checkpoint_path, all_screens
    ):
        """Run FlowRunner per platform after the link-following sessions.

        Captures the journeys a crawl must COMPLETE rather than stumble into:
        add-to-cart → cart-with-items → checkout-start, search → results,
        login/account, and configured site-specific journeys.
        """
        coverage_cfg = getattr(self.config, "coverage", None)
        if not coverage_cfg or not coverage_cfg.enabled or not coverage_cfg.run_flows:
            return

        for platform in self.config.platforms:
            if not platform.is_web:
                continue
            session_id = f"flows_{platform.type}"
            if session_id in completed_session_ids:
                logger.info(f"Skipping completed flows session: {session_id}")
                continue

            flow_dir = output_dir / session_id
            pdp_urls = self._discover_urls(output_dir, "pdp", platform.type)
            logger.info(
                f"Directed flows on {platform.type}: {len(pdp_urls)} PDP candidates"
            )
            try:
                runner = FlowRunner(
                    config=self.config,
                    output_dir=str(flow_dir),
                    platform=platform,
                    browser_type=self.browser_type,
                    cookie_jar=cookie_jar,
                )
                journey = await runner.run(
                    pdp_urls=pdp_urls, search_term=coverage_cfg.search_term
                )
                journey.save(str(flow_dir / "journey.json"))
                all_screens.extend(self._extract_screens_from_journey(journey))

                completed_session_ids.add(session_id)
                if checkpoint_path is not None:
                    self._save_checkpoint(
                        checkpoint_path, completed_session_ids, all_screens, 0
                    )
            except Exception as e:
                logger.error(f"Directed flows failed on {platform.type}: {e}", exc_info=True)

    def _discover_urls(self, output_dir: Path, page_type: str, platform_type: str, limit: int = 10):
        """Collect URLs of a given page type from journey.json files in the run.

        Prefers URLs captured on the same platform; falls back to any platform.
        """
        same_platform, other = [], []
        for jf in sorted(Path(output_dir).glob("**/journey.json")):
            try:
                data = json.loads(jf.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            jf_platform = (data.get("platform") or {}).get("type")
            for step in data.get("steps", []):
                url = step.get("url")
                if not url or PageClassifier.classify_url(url) != page_type:
                    continue
                bucket = same_platform if jf_platform == platform_type else other
                if url not in bucket:
                    bucket.append(url)
        combined = same_platform + [u for u in other if u not in same_platform]
        return combined[:limit]

    def _emit_coverage(self, output_dir: Path):
        """Generate coverage.json + coverage.md and log the found/missed table."""
        coverage_cfg = getattr(self.config, "coverage", None)
        if not coverage_cfg or not coverage_cfg.enabled:
            return None
        try:
            report = CoverageReporter(self.config).evaluate(output_dir)
            logger.info(
                f"Journey coverage: {report['summary']['found']}/"
                f"{report['summary']['total']} found"
            )
            for line in CoverageReporter.render_table(report).splitlines():
                logger.info(line)
            return report
        except Exception as e:
            logger.error(f"Coverage report failed: {e}", exc_info=True)
            return None

    def _use_crawlee(self) -> bool:
        """Whether to use CrawleeAdapter based on engine setting."""
        if self.engine == "local":
            return False
        if self.engine == "crawlee":
            return True
        # auto: use crawlee if available
        return _CRAWLEE_AVAILABLE

    def _auth_states(self) -> List[str]:
        """Get list of auth states to crawl."""
        states = []
        if self.config.auth.logged_out:
            states.append("logged_out")
        if self.config.auth.logged_in:
            states.append("logged_in")
        return states or ["logged_out"]  # Default to logged_out

    def _save_checkpoint(
        self, path: Path, completed: set, screens: List, proxy_slot: int
    ):
        """Save checkpoint for resume capability (atomic write)."""
        try:
            data = {
                "completed_sessions": list(completed),
                "total_screens": len(screens),
                "last_proxy_slot": proxy_slot,
                "saved_at": datetime.utcnow().isoformat(),
            }
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2))
            tmp_path.replace(path)  # Atomic on POSIX
        except Exception as e:
            logger.warning(f"Checkpoint save failed: {e}")

    def _extract_screens_from_journey(self, journey) -> List[Dict]:
        """
        Extract screen data from Journey object.

        Args:
            journey: Journey object from autonomous_crawler

        Returns:
            List of screen dictionaries
        """
        screens = []
        for step in journey.steps:
            screen = {
                "screen_id": f"screen_{len(screens):04d}",
                "url": step.url,
                "title": step.title,
                "screenshot_path": step.screenshot_path,
                "timestamp": step.timestamp if isinstance(step.timestamp, str) else (step.timestamp.isoformat() if step.timestamp else None),
                "page_type": getattr(step, "page_type", None) or "unknown",
                "page_data": step.page_data if hasattr(step, "page_data") else {},
            }
            screens.append(screen)
        return screens

    def _build_context(
        self, screens: List[Dict], run_id: str, output_dir: Path
    ) -> Dict:
        """
        Build final context output.

        Args:
            screens: List of captured screens
            run_id: Crawl run ID
            output_dir: Output directory

        Returns:
            Context dictionary
        """
        context = {
            "meta": {
                "run_id": run_id,
                "site_name": self.config.target.get("name", "Unknown"),
                "base_url": self.config.base_url,
                "crawled_at": datetime.utcnow().isoformat(),
                "total_screens": len(screens),
                "platforms": [p.type for p in self.config.platforms],
                "session_mode": self.config.session_strategy.mode,
            },
            "screens": screens,
        }

        # Save context to disk
        context_file = output_dir / "context.json"
        context_file.write_text(json.dumps(context, indent=2, default=str))
        logger.info(f"Context saved to {context_file}")

        return context

    async def resume(self, resume_path: str) -> Dict:
        """
        Resume a previously interrupted crawl from checkpoint.

        Args:
            resume_path: Path to the run directory containing checkpoint

        Returns:
            Crawl results dictionary
        """
        run_dir = Path(resume_path)
        if not run_dir.exists():
            raise ValueError(f"Resume path does not exist: {resume_path}")

        checkpoint_path = run_dir / "checkpoint.json"
        if not checkpoint_path.exists():
            raise ValueError(f"No checkpoint found in: {resume_path}")

        # Load checkpoint to get run_id
        checkpoint = json.loads(checkpoint_path.read_text())
        self.config.run_id = run_dir.name
        self.config.output_dir = str(run_dir.parent)

        logger.info(
            f"Resuming crawl: {len(checkpoint.get('completed_sessions', []))} "
            f"sessions complete, {checkpoint.get('total_screens', 0)} screens captured"
        )

        # Run will automatically skip completed sessions
        return await self.run_all()
