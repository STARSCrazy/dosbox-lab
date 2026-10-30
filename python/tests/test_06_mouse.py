import ctypes
import time
import unittest
from ctypes import wintypes

from tests.util import class_box, need_build, start_prog, wait_until


def cursor_and_foreground():
    point = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y), ctypes.windll.user32.GetForegroundWindow()


def mouse_block(box):
    data = box.mem_read("l:4F4", 10)
    words = [int.from_bytes(data[i:i + 2], "little", signed=True) for i in range(0, 10, 2)]
    return {"x": words[0], "y": words[1], "knoepfe": words[2], "mx": words[3], "my": words[4]}


@need_build
class MouseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = cursor_and_foreground()
        # Sichtbares Fenster: echter Praxisfall. Waehrend dieses Tests die echte Maus nicht bewegen,
        # sonst schlaegt test_real_cursor_untouched zu Recht fehl.
        cls.box = class_box(cls, start_prog("MOUSE.COM", headless=False))
        time.sleep(1.0)


    def test_move_in_game_pixels(self):
        result = self.box.mouse_move(160, 100)            # Modus 13h: 320x200 Spielpixel
        self.assertTrue(result["erreicht"], result)
        block = wait_until(lambda: (b := mouse_block(self.box))["x"] == 320 and b)
        self.assertEqual(block["y"], 100)

    def test_mickeys_are_generated(self):
        start = mouse_block(self.box)
        self.box.mouse_move_rel(40, 0)
        block = wait_until(lambda: (b := mouse_block(self.box))["mx"] != start["mx"] and b)
        self.assertGreater(block["mx"], start["mx"])

    def test_move_beyond_range_is_clamped(self):
        self.box.mouse_move(160, 100)
        start = mouse_block(self.box)
        result = self.box.mouse_move(319, 199 + 50)        # 50 Spielpixel unterhalb des Bildes
        max_y = int(result["bereich"]["max_y"])
        self.assertTrue(result.get("geklemmt"), result)
        self.assertTrue(result["erreicht"], result)
        self.assertEqual(int(result["int33"]["y"]), max_y)
        block = wait_until(lambda: (b := mouse_block(self.box))["y"] == max_y and b)
        self.assertLess(abs(block["my"] - start["my"]), 2 * (max_y + 1) * 2)
        self.assertNotIn("geklemmt", self.box.mouse_move(160, 100))

    def test_relative_matches_absolute(self):
        self.box.mouse_move(100, 100)
        before = self.box.mouse_state()["int33"]
        after = self.box.mouse_move_rel(40, 20)["int33"]
        self.assertAlmostEqual(after["x"] - before["x"], 40, delta=1)
        self.assertAlmostEqual(after["y"] - before["y"], 20, delta=1)

    def test_click(self):
        self.box.mouse_click(50, 50)
        wait_until(lambda: mouse_block(self.box)["knoepfe"] & 1)

    def test_real_cursor_untouched(self):
        self.box.mouse_move(10, 10)
        self.box.mouse_click(300, 190, button="rechts")
        self.box.key("A")
        self.box.wait_input()
        self.assertEqual(cursor_and_foreground(), self.before)


if __name__ == "__main__":
    unittest.main()
