"""Per-page human behavior orchestration.

Runs a realistic sequence of scroll, mouse movement, hover, and dwell
on each captured page. Uses HumanBehaviour primitives. This triggers
JS beacons that prove user engagement to anti-bot systems.
"""

import logging
import random

from ux_journey_scraper.core.human_behaviour import HumanBehaviour

logger = logging.getLogger(__name__)


class BehaviorSequencer:
    """Runs human behavior simulation on each page after readiness."""

    DWELL_TIMES = {
        "pdp": (40, 90),
        "plp": (20, 50),
        "homepage": (15, 40),
        "search": (15, 35),
        "cart": (20, 45),
        "checkout": (30, 60),
        "policy": (10, 25),
        "info": (10, 25),
        "content": (20, 50),
        "other": (15, 35),
    }

    async def run(self, page, page_type: str = "other") -> None:
        """Execute full behavior sequence on a page.

        Order:
        1. Scroll down to random depth (30-80% of page)
        2. Move mouse to 3-5 random positions (Bezier curves)
        3. Hover over 1-2 interactive elements
        4. Dwell for page-type-appropriate duration
        5. Scroll back to top (ready for screenshot)
        """
        viewport = page.viewport_size or {"width": 1920, "height": 1080}
        vw = viewport["width"]
        vh = viewport["height"]

        try:
            page_height = await page.evaluate("document.body.scrollHeight")
        except Exception:
            page_height = vh

        # 1. Scroll to random depth (30-80% of page)
        scroll_target = int(page_height * random.uniform(0.3, 0.8))
        try:
            await HumanBehaviour.human_scroll(page, direction="down", distance=scroll_target)
        except Exception as e:
            logger.debug(f"Behavior scroll failed: {e}")

        # 2. Move mouse to 3-5 random positions
        num_moves = random.randint(3, 5)
        for _ in range(num_moves):
            x, y = HumanBehaviour.random_viewport_position(vw, vh)
            try:
                await HumanBehaviour.human_mouse_move(page, to_x=x, to_y=y)
            except Exception as e:
                logger.debug(f"Behavior mouse move failed: {e}")

        # 3. Hover over 1-2 interactive elements
        hover_selectors = ["a:not([aria-hidden='true'])", "button:not([disabled])"]
        num_hovers = random.randint(1, 2)
        for i in range(num_hovers):
            selector = hover_selectors[i % len(hover_selectors)]
            try:
                elements = await page.query_selector_all(selector)
                visible = []
                for el in elements[:20]:
                    try:
                        if await el.is_visible():
                            visible.append(el)
                    except Exception:
                        pass
                if visible:
                    target = random.choice(visible)
                    box = await target.bounding_box()
                    if box:
                        tx = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
                        ty = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
                        await HumanBehaviour.human_mouse_move(page, to_x=tx, to_y=ty)
                        await HumanBehaviour.human_delay(300, 800, reason="scroll_pause")
            except Exception as e:
                logger.debug(f"Behavior hover failed: {e}")

        # 4. Dwell for page-type-appropriate duration
        min_dwell, max_dwell = self.DWELL_TIMES.get(page_type, self.DWELL_TIMES["other"])
        dwell_ms = random.randint(min_dwell * 1000, max_dwell * 1000)
        try:
            await HumanBehaviour.human_delay(dwell_ms, dwell_ms + 2000, reason="page_load")
        except Exception as e:
            logger.debug(f"Behavior dwell failed: {e}")

        # 5. Scroll back to top
        try:
            await HumanBehaviour.human_scroll(page, direction="to_top")
        except Exception as e:
            logger.debug(f"Behavior scroll-to-top failed: {e}")

        logger.debug(f"Behavior sequence complete: {page_type}, dwell={dwell_ms}ms")
