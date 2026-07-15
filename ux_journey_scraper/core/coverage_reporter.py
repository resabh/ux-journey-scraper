"""Journey coverage report — expected journeys vs what a crawl captured.

Reads every journey.json under a run directory, evaluates a per-site checklist
of expected journeys, and emits:

    coverage.json — machine-readable found/missed with evidence
    coverage.md   — human-readable table for review

Coverage (found/missed journeys) is the progress metric for crawls — page
counts alone say nothing about whether cart/checkout/search were captured.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COVERAGE_SCHEMA_VERSION = "1.0"

REQUIRED_PAGE_TYPES = {"homepage", "plp", "pdp", "search_results", "cart"}
PAGE_TYPE_ALIASES = {"search": "search_results"}

# Default e-commerce journey checklist. Detection precedence per journey:
#   flow_tags    — step captured by FlowRunner with page_data.flow set
#                  (proof the flow was COMPLETED, not just a URL match)
#   page_types   — step whose page_data.page_type matches
#   url_patterns — regex match on step URL (lowercased)
# cart_with_items deliberately has NO page_type fallback: a bare /cart visit
# (usually empty) must not count as a cart-with-items capture.
DEFAULT_JOURNEYS = [
    {"id": "homepage", "label": "Homepage", "page_types": ["homepage"]},
    {"id": "browse_plp", "label": "Browse → category listing (PLP)", "page_types": ["plp"]},
    {"id": "browse_pdp", "label": "Browse → product page (PDP)", "page_types": ["pdp"]},
    {
        "id": "add_to_cart",
        "label": "Add-to-cart action completed",
        "flow_tags": ["add_to_cart"],
    },
    {
        "id": "cart_with_items",
        "label": "Cart with items",
        "flow_tags": ["cart_with_items"],
    },
    {
        "id": "checkout_start",
        "label": "Checkout started",
        "flow_tags": ["checkout_start"],
        "page_types": ["checkout"],
    },
    {
        "id": "search_results",
        "label": "Search → results",
        "flow_tags": ["search_results"],
        "page_types": ["search"],
    },
    {
        "id": "login_account",
        "label": "Login / account",
        "flow_tags": ["login_page"],
        "page_types": ["account"],
        "url_patterns": [r"/account", r"/login", r"/sign-?in"],
    },
    {"id": "wishlist", "label": "Wishlist", "url_patterns": [r"wishlist"]},
    {
        "id": "order_tracking",
        "label": "Order tracking",
        "url_patterns": [r"track", r"/orders?(/|$|\?)"],
    },
    {"id": "policy_pages", "label": "Policy pages", "page_types": ["policy"]},
]


class CoverageReporter:
    """Evaluate journey coverage for a crawl run directory."""

    def __init__(self, config=None):
        """
        Args:
            config: Optional ScrapeConfig — supplies site name and
                coverage.site_journeys extras.
        """
        self.config = config
        self.journeys = [dict(j) for j in DEFAULT_JOURNEYS]
        site_journeys = getattr(getattr(config, "coverage", None), "site_journeys", [])
        for entry in site_journeys:
            extra = {
                "id": entry.get("id", "site_specific"),
                "label": entry.get("label", entry.get("id", "Site-specific journey")),
                "site_specific": True,
            }
            if entry.get("url_patterns"):
                extra["url_patterns"] = entry["url_patterns"]
            # FlowRunner tags directly-visited site journeys with their id
            extra["flow_tags"] = [extra["id"]]
            self.journeys.append(extra)

    def evaluate(self, run_dir, write: bool = True) -> dict:
        """Evaluate coverage over all journey.json files under run_dir.

        Args:
            run_dir: Crawl run directory (contains session subdirectories).
            write: Whether to write coverage.json and coverage.md into run_dir.

        Returns:
            Coverage report dict.
        """
        run_dir = Path(run_dir)
        steps = []  # (session_id, platform_type, journey_file, step_dict)
        validation = {}
        platforms = set()

        journey_files = sorted(run_dir.glob("**/journey.json"))
        for jf in journey_files:
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                validation[str(jf.relative_to(run_dir))] = [f"unreadable: {e}"]
                continue

            session_id = jf.parent.name
            platform_type = (data.get("platform") or {}).get("type") or "unknown"
            platforms.add(platform_type)
            for step in data.get("steps", []):
                steps.append((session_id, platform_type, jf, step))

            try:
                from ux_journey_scraper.core.schema_validator import (
                    check_screenshot_dimensions,
                    validate_journey_dict,
                )

                errors = validate_journey_dict(data)
                errors += check_screenshot_dimensions(jf, data)
                validation[str(jf.relative_to(run_dir))] = errors
            except Exception as e:
                validation[str(jf.relative_to(run_dir))] = [f"validation skipped: {e}"]

        platforms = sorted(platforms)
        results = []
        for jdef in self.journeys:
            evidence = self._find_evidence(jdef, steps)
            found_platforms = sorted({e["platform"] for e in evidence})
            results.append(
                {
                    "id": jdef["id"],
                    "label": jdef["label"],
                    "site_specific": jdef.get("site_specific", False),
                    "status": "found" if evidence else "missed",
                    "platforms_found": found_platforms,
                    "platforms_missing": [p for p in platforms if p not in found_platforms],
                    "evidence": evidence[:5],
                }
            )

        page_type_distribution = {}
        for _, platform_type, _, step in steps:
            pt = (step.get("page_data") or {}).get("page_type") or "unknown"
            page_type_distribution.setdefault(platform_type, {})
            page_type_distribution[platform_type][pt] = (
                page_type_distribution[platform_type].get(pt, 0) + 1
            )

        found = sum(1 for r in results if r["status"] == "found")
        site_name = "unknown"
        if self.config is not None:
            site_name = self.config.target.get("name", "unknown")

        report = {
            "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
            "site": site_name,
            "run_dir": str(run_dir),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "platforms": platforms,
            "journey_files": len(journey_files),
            "total_steps": len(steps),
            "journeys": results,
            "summary": {
                "found": found,
                "missed": len(results) - found,
                "total": len(results),
            },
            "page_type_distribution": page_type_distribution,
            "schema_validation": {
                "valid_files": sum(1 for v in validation.values() if not v),
                "invalid_files": sum(1 for v in validation.values() if v),
                "errors": {k: v for k, v in validation.items() if v},
            },
        }

        if write:
            (run_dir / "coverage.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (run_dir / "coverage.md").write_text(self.render_table(report), encoding="utf-8")
            logger.info(f"Coverage report written: {run_dir / 'coverage.json'}")

        return report

    @staticmethod
    def _find_evidence(jdef: dict, steps) -> list:
        evidence = []
        patterns = [re.compile(p, re.I) for p in jdef.get("url_patterns", [])]
        flow_tags = set(jdef.get("flow_tags", []))
        page_types = set(jdef.get("page_types", []))

        for session_id, platform_type, jf, step in steps:
            page_data = step.get("page_data") or {}
            url = (step.get("url") or "").lower()
            matched_by = None
            if flow_tags and page_data.get("flow") in flow_tags:
                matched_by = f"flow:{page_data['flow']}"
            elif page_types and page_data.get("page_type") in page_types:
                matched_by = f"page_type:{page_data['page_type']}"
            elif patterns and any(p.search(url) for p in patterns):
                matched_by = "url_pattern"
            if matched_by:
                evidence.append(
                    {
                        "session": session_id,
                        "platform": platform_type,
                        "step_number": step.get("step_number"),
                        "url": step.get("url"),
                        "matched_by": matched_by,
                    }
                )
        return evidence

    def emit_readiness(self, run_dir, config=None) -> dict:
        """Evaluate and write readiness.json per platform directory.

        Non-blocking: benchmark_ready=false is informational, never aborts.

        Args:
            run_dir: Crawl run directory.
            config: Optional ScrapeConfig for location check.

        Returns:
            Dict mapping platform_dir_name -> readiness result.
        """
        run_dir = Path(run_dir)
        cfg = config or self.config
        results = {}

        for journey_file in sorted(run_dir.glob("**/journey.json")):
            platform_dir = journey_file.parent
            try:
                data = json.loads(journey_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"Readiness skip {journey_file}: {e}")
                continue

            steps = data.get("steps", [])
            if not steps:
                continue

            page_types = set()
            nav_populated = False
            forms_populated = False
            search_detected = False
            screenshots_ok = True
            blocked_steps = []

            for step in steps:
                pd = step.get("page_data", {})

                # Skip block/error pages — they must not count toward coverage
                # or quality signals (S1.10 item 6 / page_state).
                if pd.get("page_state") == "blocked" or \
                        pd.get("response_metadata", {}).get("blocked"):
                    blocked_steps.append(step.get("step_number"))
                    continue

                pt = pd.get("page_type", "other")
                pt = PAGE_TYPE_ALIASES.get(pt, pt)
                page_types.add(pt)

                nav = pd.get("navigation", {})
                if nav.get("primary_nav"):
                    nav_populated = True

                if pd.get("forms"):
                    forms_populated = True

                search = pd.get("search", {})
                if search.get("has_search_bar"):
                    search_detected = True

                ss_path = step.get("screenshot_path")
                if not ss_path:
                    screenshots_ok = False
                else:
                    from ux_journey_scraper.core.screenshot_manager import validate_screenshot
                    resolved = Path(ss_path)
                    if not resolved.is_absolute():
                        resolved = platform_dir / resolved
                    is_valid, _ = validate_screenshot(resolved)
                    if not is_valid:
                        screenshots_ok = False

            no_blocked_steps = len(blocked_steps) == 0

            present = sorted(page_types & REQUIRED_PAGE_TYPES)
            missing = sorted(REQUIRED_PAGE_TYPES - page_types)

            schema_valid = True
            try:
                from ux_journey_scraper.core.schema_validator import (
                    validate_journey_dict,
                )
                errors = validate_journey_dict(data)
                schema_valid = len(errors) == 0
            except Exception:
                schema_valid = False

            location_established = True
            if cfg and hasattr(cfg, "location") and cfg.location.pincode:
                location_established = bool(data.get("location_verified", False))

            # S1.14: detect byte-identical duplicate frames
            frame_hashes = {}
            duplicate_groups = []
            for step in steps:
                ss = step.get("screenshot_path")
                if not ss:
                    continue
                ss_resolved = Path(ss)
                if not ss_resolved.is_absolute():
                    ss_resolved = platform_dir / ss_resolved
                if ss_resolved.exists():
                    h = hashlib.sha256(ss_resolved.read_bytes()).hexdigest()
                    frame_hashes.setdefault(h, []).append(step.get("step_number"))
            for h, step_nums in frame_hashes.items():
                if len(step_nums) > 1:
                    duplicate_groups.append(step_nums)

            benchmark_ready = (
                len(missing) == 0
                and nav_populated
                and (forms_populated or search_detected)
                and screenshots_ok
                and schema_valid
                and location_established
                and no_blocked_steps
            )

            readiness = {
                "page_types_present": present,
                "page_types_required": sorted(REQUIRED_PAGE_TYPES),
                "missing_page_types": missing,
                "primary_nav_populated": nav_populated,
                "forms_populated": forms_populated,
                "search_detected": search_detected,
                "screenshots_per_step": screenshots_ok,
                "schema_valid": schema_valid,
                "location_established": location_established,
                "no_blocked_steps": no_blocked_steps,
                "environment": data.get("environment", "prod"),
                "capture_start": data.get("start_time", ""),
                "capture_end": data.get("end_time", ""),
                "benchmark_ready": benchmark_ready,
            }
            if blocked_steps:
                readiness["blocked_steps"] = blocked_steps
            if duplicate_groups:
                readiness["duplicate_frames"] = duplicate_groups

            out_path = platform_dir / "readiness.json"
            out_path.write_text(
                json.dumps(readiness, indent=2), encoding="utf-8"
            )
            status = "READY" if benchmark_ready else "NOT READY"
            logger.info(
                f"Readiness [{platform_dir.name}]: {status} "
                f"types={present} missing={missing}"
            )
            results[platform_dir.name] = readiness

        # Combined readiness per base platform (merge crawl + flows)
        platform_groups = {}
        for dir_name, r in results.items():
            base = dir_name.replace("flows_", "")
            if base not in platform_groups:
                platform_groups[base] = {
                    "page_types": set(),
                    "nav_populated": False,
                    "forms_populated": False,
                    "search_detected": False,
                    "screenshots_ok": True,
                    "schema_valid": True,
                    "location_established": True,
                    "no_blocked_steps": True,
                    "environment": "prod",
                    "dirs": [],
                }
            g = platform_groups[base]
            g["page_types"].update(r.get("page_types_present", []))
            g["nav_populated"] = g["nav_populated"] or r.get("primary_nav_populated", False)
            g["forms_populated"] = g["forms_populated"] or r.get("forms_populated", False)
            g["search_detected"] = g["search_detected"] or r.get("search_detected", False)
            g["screenshots_ok"] = g["screenshots_ok"] and r.get("screenshots_per_step", True)
            g["schema_valid"] = g["schema_valid"] and r.get("schema_valid", True)
            g["location_established"] = g["location_established"] and r.get("location_established", True)
            g["no_blocked_steps"] = g["no_blocked_steps"] and r.get("no_blocked_steps", True)
            g["environment"] = r.get("environment", g["environment"])
            g["dirs"].append(dir_name)

        for base, g in platform_groups.items():
            present = sorted(g["page_types"] & REQUIRED_PAGE_TYPES)
            missing = sorted(REQUIRED_PAGE_TYPES - g["page_types"])
            location_established = g["location_established"]
            benchmark_ready = (
                len(missing) == 0
                and g["nav_populated"]
                and (g["forms_populated"] or g["search_detected"])
                and g["screenshots_ok"]
                and g["schema_valid"]
                and location_established
                and g["no_blocked_steps"]
            )
            combined = {
                "page_types_present": present,
                "page_types_required": sorted(REQUIRED_PAGE_TYPES),
                "missing_page_types": missing,
                "primary_nav_populated": g["nav_populated"],
                "forms_populated": g["forms_populated"],
                "search_detected": g["search_detected"],
                "screenshots_per_step": g["screenshots_ok"],
                "schema_valid": g["schema_valid"],
                "location_established": location_established,
                "no_blocked_steps": g["no_blocked_steps"],
                "environment": g["environment"],
                "benchmark_ready": benchmark_ready,
                "combined_from": g["dirs"],
            }
            out_path = run_dir / f"readiness_{base}.json"
            out_path.write_text(
                json.dumps(combined, indent=2), encoding="utf-8"
            )
            status = "READY" if benchmark_ready else "NOT READY"
            logger.info(
                f"Readiness [{base} combined]: {status} "
                f"types={present} missing={missing}"
            )
            results[f"{base}_combined"] = combined

        return results

    @staticmethod
    def render_table(report: dict) -> str:
        """Render the human-readable found/missed coverage table (markdown)."""
        platforms = report["platforms"] or ["(none)"]
        lines = [
            f"# Journey coverage — {report['site']}",
            "",
            f"Run: `{report['run_dir']}`  ",
            f"Generated: {report['generated_at']}  ",
            f"Coverage: **{report['summary']['found']}/{report['summary']['total']} journeys found** "
            f"across {report['journey_files']} journey files / {report['total_steps']} steps.",
            "",
            "| Journey | " + " | ".join(platforms) + " | Evidence |",
            "|---" * (len(platforms) + 2) + "|",
        ]
        for r in report["journeys"]:
            cells = []
            for p in platforms:
                if p in r["platforms_found"]:
                    cells.append("✅")
                elif r["status"] == "found":
                    cells.append("❌")
                else:
                    cells.append("—")
            ev = r["evidence"][0]["url"] if r["evidence"] else ""
            label = r["label"] + (" *(site-specific)*" if r["site_specific"] else "")
            status = "" if r["status"] == "found" else " **MISSED**"
            lines.append(f"| {label}{status} | " + " | ".join(cells) + f" | {ev} |")

        sv = report["schema_validation"]
        lines += [
            "",
            f"Schema validation: {sv['valid_files']} valid / {sv['invalid_files']} invalid journey files.",
        ]
        for path, errors in sv["errors"].items():
            lines.append(f"- `{path}`: {len(errors)} error(s); first: {errors[0]}")

        lines += ["", "## Page type distribution", ""]
        for platform_type, dist in sorted(report["page_type_distribution"].items()):
            ordered = sorted(dist.items(), key=lambda kv: -kv[1])
            lines.append(f"- **{platform_type}**: " + ", ".join(f"{pt}={n}" for pt, n in ordered))
        lines.append("")
        return "\n".join(lines)
