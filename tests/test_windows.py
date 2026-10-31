"""Capture-path tests use fake Win32 calls; never access desktop input."""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1]))
from buckshot import windows


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.bounds = (100, 200, 100, 100)
        self.user = Mock()

        def window_rect(hwnd, pointer):
            self.assertEqual(hwnd, 77)
            rect = pointer._obj
            rect.left, rect.top, rect.right, rect.bottom = 90, 180, 210, 320
            return True

        self.user.GetWindowRect.side_effect = window_rect
        self.api = patch.object(windows, "_api", return_value=(self.user, None, None))
        self.dpi = patch.object(windows, "_physical_pixels", side_effect=contextlib.nullcontext)
        self.api.start()
        self.dpi.start()
        self.addCleanup(self.api.stop)
        self.addCleanup(self.dpi.stop)

    def test_offscreen_render_crops_client_and_ignores_desktop_overlay(self):
        outer = Image.new("RGB", (120, 140), (20, 30, 40))
        outer.putpixel((15, 25), (255, 0, 0))
        with patch.object(windows, "_assert_game", return_value=self.bounds), \
             patch.object(windows, "_bitmap_image", return_value=outer) as bitmap, \
             patch.object(windows, "_assert_unobscured", side_effect=AssertionError("must not read screen")):
            image = windows.capture(77)
        self.assertEqual(image.size, (100, 100))
        self.assertEqual(image.getpixel((5, 5)), (255, 0, 0))
        bitmap.assert_called_once_with(77, 120, 140, render_full=True)

    def test_flat_offscreen_frame_falls_back_only_with_unobscured_client(self):
        flat = Image.new("RGB", (120, 140))
        client = Image.new("RGB", (100, 100), (30, 40, 50))
        client.putpixel((10, 10), (250, 250, 250))
        with patch.object(windows, "_assert_game", return_value=self.bounds), \
             patch.object(windows, "_bitmap_image", side_effect=[flat, client]) as bitmap, \
             patch.object(windows, "_assert_unobscured") as guard:
            image = windows.capture(77)
        self.assertEqual(image.getpixel((10, 10)), (250, 250, 250))
        self.assertEqual(guard.call_count, 2)
        self.assertEqual(bitmap.call_args_list[1].kwargs, {"render_full": False})

    def test_overlay_blocks_fallback_before_any_client_capture(self):
        flat = Image.new("RGB", (120, 140))
        with patch.object(windows, "_assert_game", return_value=self.bounds), \
             patch.object(windows, "_bitmap_image", return_value=flat) as bitmap, \
             patch.object(windows, "_assert_unobscured", side_effect=windows.GameWindowError("obscured")):
            with self.assertRaisesRegex(windows.GameWindowError, "obscured"):
                windows.capture(77)
        self.assertEqual(bitmap.call_count, 1)

    def test_losing_focus_discards_successful_offscreen_render(self):
        outer = Image.new("RGB", (120, 140), (20, 30, 40))
        outer.putpixel((15, 25), (255, 0, 0))
        with patch.object(windows, "_assert_game", side_effect=[self.bounds, windows.GameWindowError("focus lost")]), \
             patch.object(windows, "_bitmap_image", return_value=outer):
            with self.assertRaisesRegex(windows.GameWindowError, "focus lost"):
                windows.capture(77)

    def test_nonforeground_window_never_starts_capture(self):
        with patch.object(windows, "_assert_game", side_effect=windows.GameWindowError("focus lost")), \
             patch.object(windows, "_bitmap_image") as bitmap:
            with self.assertRaises(windows.GameWindowError):
                windows.capture(77)
        bitmap.assert_not_called()


class KeyTests(unittest.TestCase):
    def test_key_has_bounded_dwell_and_releases(self):
        sent = []
        with patch.object(windows, "_physical_pixels", side_effect=contextlib.nullcontext), \
             patch.object(windows, "_assert_game"), \
             patch.object(windows, "_assert_modifiers_released"), \
             patch.object(windows, "_send", side_effect=lambda event: sent.append((event.ki.wVk, event.ki.dwFlags))), \
             patch.object(windows.time, "sleep") as sleep:
            windows.key(77, "W")
        self.assertEqual(sent, [(0x57, 0), (0x57, 2)])
        sleep.assert_called_once_with(.08)

    def test_key_releases_even_if_dwell_is_interrupted(self):
        sent = []
        with patch.object(windows, "_physical_pixels", side_effect=contextlib.nullcontext), \
             patch.object(windows, "_assert_game"), \
             patch.object(windows, "_assert_modifiers_released"), \
             patch.object(windows, "_send", side_effect=lambda event: sent.append((event.ki.wVk, event.ki.dwFlags))), \
             patch.object(windows.time, "sleep", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                windows.key(77, "ENTER")
        self.assertEqual(sent, [(0x0D, 0), (0x0D, 2)])


if __name__ == "__main__":
    unittest.main()
