import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from dosbox_lab import DmcpError
from dosbox_lab.png import pixel, read_rgb
from tests.util import class_box, need_build, start_prog, wait_until

PYDIR = Path(__file__).resolve().parents[1]


def close(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@need_build
class VideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("VIDEO.COM"))
        wait_until(lambda: cls.box.video_state()["mode"] == "13", timeout=15)


    def test_full_screenshot(self):
        info = wait_until(lambda: self._try_shot())
        image = read_rgb(info["path"])
        self.assertEqual(image[:2], (320, 200))
        self.assertTrue(close(pixel(image, 50, 100), (255, 0, 0)), pixel(image, 50, 100))
        self.assertTrue(close(pixel(image, 160, 100), (0, 255, 0)), pixel(image, 160, 100))
        self.assertTrue(close(pixel(image, 270, 100), (0, 0, 255)), pixel(image, 270, 100))
        self.assertEqual(info["sha256"], hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest())

    def _try_shot(self):
        info = self.box.screenshot("full")
        image = read_rgb(info["path"])
        return info if close(pixel(image, 50, 100), (255, 0, 0)) else None

    def test_crop(self):
        info = self.box.screenshot("crop", rect=(150, 10, 20, 30))
        image = read_rgb(info["path"])
        self.assertEqual(image[:2], (20, 30))
        self.assertTrue(close(pixel(image, 5, 5), (0, 255, 0)))

    def test_rect_outside(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.screenshot("wrong", rect=(300, 190, 50, 50))
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_rect_overflow(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("screen.capture", name="overflow", x=18446744073709551615, y=0, width=2, height=2)
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_text_mode_refused(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.screen_text()
        self.assertEqual(ctx.exception.code, "not_text_mode")

    def test_paused_screenshot(self):
        self.box.pause()
        try:
            info = self.box.screenshot("pause")
            self.assertEqual((info["width"], info["height"]), (320, 200))
        finally:
            self.box.resume()

    def test_factor_reported(self):
        info = self.box.screenshot("factor")
        q = info["source"]
        self.assertEqual(q["width"], 320 * info["factor"]["x"])
        self.assertEqual(q["height"], 200 * info["factor"]["y"])


@need_build
class TextTest(unittest.TestCase):
    def test_text_screen(self):
        box = start_prog("echo DMCP-TEXT-OK")
        try:
            def try_text():
                try:
                    text = box.screen_text()
                except DmcpError as e:  # right after startup the text mode is not set yet
                    if e.code != "not_text_mode":
                        raise
                    return None
                return text if any("DMCP-TEXT-OK" in z for z in text["lines"]) else None

            text = wait_until(try_text)
            self.assertEqual(text["columns"], 80)
        finally:
            box.stop()

    def test_cp437_unicode(self):
        # TEXTCP.COM writes C9 CD 84 01 7F 00 (attribute 07) to B800:0640 = line 10; mem.write does not
        # reach the VGA memory (system memory).
        box = start_prog("TEXTCP.COM")
        self.addCleanup(box.stop)
        text = wait_until(lambda: (r := self._row10(box)) and r.endswith("⌂") and r)
        self.assertTrue(text.startswith("╔═ä☺⌂"), text)
        self.assertEqual(text, "╔═ä☺⌂")  # 0x00 -> space (trailing part truncated)

    def test_cli_text_is_ascii_json(self):
        box = start_prog("TEXTCP.COM")
        self.addCleanup(box.stop)
        wait_until(lambda: (r := self._row10(box)) and r.endswith("⌂") and r)
        proc = subprocess.run([sys.executable, "-m", "dosbox_lab", "text", "--id", box.inst.id],
                              cwd=PYDIR, capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.isascii())
        self.assertEqual(json.loads(proc.stdout.decode("ascii"))["lines"][10], "╔═ä☺⌂")

    @staticmethod
    def _row10(box):
        try:
            row = box.screen_text()["lines"][10]
        except DmcpError:
            return None
        return row or None


if __name__ == "__main__":
    unittest.main()
