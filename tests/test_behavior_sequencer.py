"""Tests for BehaviorSequencer — per-page human behavior orchestration."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ux_journey_scraper.core.behavior_sequencer import BehaviorSequencer


class TestBehaviorSequencer(unittest.TestCase):

    def test_dwell_times_defined_for_all_types(self):
        seq = BehaviorSequencer()
        for page_type in ["pdp", "plp", "homepage", "search", "cart", "checkout", "policy", "info", "content", "other"]:
            self.assertIn(page_type, seq.DWELL_TIMES)
            min_t, max_t = seq.DWELL_TIMES[page_type]
            self.assertGreater(min_t, 0)
            self.assertGreater(max_t, min_t)

    def test_dwell_time_pdp_longer_than_policy(self):
        seq = BehaviorSequencer()
        pdp_min, _ = seq.DWELL_TIMES["pdp"]
        policy_min, _ = seq.DWELL_TIMES["policy"]
        self.assertGreater(pdp_min, policy_min)

    def test_run_calls_behavior_methods(self):
        seq = BehaviorSequencer()
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.evaluate = AsyncMock(return_value=2000)
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])

        with patch("ux_journey_scraper.core.behavior_sequencer.HumanBehaviour") as mock_hb:
            mock_hb.human_scroll = AsyncMock()
            mock_hb.human_mouse_move = AsyncMock()
            mock_hb.human_hover = AsyncMock()
            mock_hb.human_delay = AsyncMock()
            mock_hb.random_viewport_position = MagicMock(return_value=(500, 400))

            asyncio.run(seq.run(page, page_type="pdp"))

            mock_hb.human_scroll.assert_called()
            mock_hb.human_mouse_move.assert_called()
            mock_hb.human_delay.assert_called()

    def test_run_scrolls_back_to_top(self):
        seq = BehaviorSequencer()
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.evaluate = AsyncMock(return_value=2000)
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])

        with patch("ux_journey_scraper.core.behavior_sequencer.HumanBehaviour") as mock_hb:
            mock_hb.human_scroll = AsyncMock()
            mock_hb.human_mouse_move = AsyncMock()
            mock_hb.human_hover = AsyncMock()
            mock_hb.human_delay = AsyncMock()
            mock_hb.random_viewport_position = MagicMock(return_value=(500, 400))

            asyncio.run(seq.run(page, page_type="other"))

            # Last scroll call should be to_top
            scroll_calls = mock_hb.human_scroll.call_args_list
            last_call = scroll_calls[-1]
            # Check kwargs for direction="to_top"
            self.assertEqual(last_call.kwargs.get("direction"), "to_top")

    def test_unknown_page_type_uses_other(self):
        seq = BehaviorSequencer()
        page = MagicMock()
        page.viewport_size = {"width": 1920, "height": 1080}
        page.evaluate = AsyncMock(return_value=2000)
        page.mouse = MagicMock()
        page.mouse.move = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])

        with patch("ux_journey_scraper.core.behavior_sequencer.HumanBehaviour") as mock_hb:
            mock_hb.human_scroll = AsyncMock()
            mock_hb.human_mouse_move = AsyncMock()
            mock_hb.human_hover = AsyncMock()
            mock_hb.human_delay = AsyncMock()
            mock_hb.random_viewport_position = MagicMock(return_value=(500, 400))

            # Should not raise
            asyncio.run(seq.run(page, page_type="totally_unknown_type"))


if __name__ == "__main__":
    unittest.main()
