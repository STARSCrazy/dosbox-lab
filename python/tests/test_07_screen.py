import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from dosbox_mcp import DmcpError
from dosbox_mcp.png import pixel, read_rgb
from tests.util import class_box, need_build, start_prog, wait_until

PYDIR = Path(__file__).resolve().parents[1]


def close(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


@need_build
class VideoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("VIDEO.COM"))
        wait_until(lambda: cls.box.video_state()["modus"] == "13", timeout=15)


    def test_full_screenshot(self):
        info = wait_until(lambda: self._try_shot())
        image = read_rgb(info["pfad"])
        self.assertEqual(image[:2], (320, 200))
        self.assertTrue(close(pixel(image, 50, 100), (255, 0, 0)), pixel(image, 50, 100))
        self.assertTrue(close(pixel(image, 160, 100), (0, 255, 0)), pixel(image, 160, 100))
        self.assertTrue(close(pixel(image, 270, 100), (0, 0, 255)), pixel(image, 270, 100))
        self.assertEqual(info["sha256"], hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest())

    def _try_shot(self):
        info = self.box.screenshot("voll")
        image = read_rgb(info["pfad"])
        return info if close(pixel(image, 50, 100), (255, 0, 0)) else None

    def test_crop(self):
        info = self.box.screenshot("ausschnitt", rect=(150, 10, 20, 30))
        image = read_rgb(info["pfad"])
        self.assertEqual(image[:2], (20, 30))
        self.assertTrue(close(pixel(image, 5, 5), (0, 255, 0)))

    def test_rect_outside(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.screenshot("falsch", rect=(300, 190, 50, 50))
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_rect_overflow(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("screen.capture", name="ueberlauf", x=18446744073709551615, y=0, breite=2, hoehe=2)
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_text_mode_refused(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.screen_text()
        self.assertEqual(ctx.exception.code, "not_text_mode")

    def test_paused_screenshot(self):
        self.box.pause()
        try:
            info = self.box.screenshot("pause")
            self.assertEqual((info["breite"], info["hoehe"]), (320, 200))
        finally:
            self.box.resume()

    def test_factor_reported(self):
        info = self.box.screenshot("faktor")
        q = info["quelle"]
        self.assertEqual(q["breite"], 320 * info["faktor"]["x"])
        self.assertEqual(q["hoehe"], 200 * info["faktor"]["y"])


@need_build
class TextTest(unittest.TestCase):
    def test_text_screen(self):
        box = start_prog("echo DMCP-TEXT-OK")
        try:
            def try_text():
                try:
                    text = box.screen_text()
                except DmcpError as e:  # direkt nach dem Start ist noch kein Textmodus gesetzt
                    if e.code != "not_text_mode":
                        raise
                    return None
                return text if any("DMCP-TEXT-OK" in z for z in text["zeilen"]) else None

            text = wait_until(try_text)
            self.assertEqual(text["spalten"], 80)
        finally:
            box.stop()

    def test_cp437_unicode(self):
        # TEXTCP.COM schreibt C9 CD 84 01 7F 00 (Attribut 07) nach B800:0640 = Zeile 10; mem.write erreicht
        # den VGA-Speicher nicht (Systemspeicher).
        box = start_prog("TEXTCP.COM")
        self.addCleanup(box.stop)
        text = wait_until(lambda: (r := self._row10(box)) and r.endswith("⌂") and r)
        self.assertTrue(text.startswith("╔═ä☺⌂"), text)
        self.assertEqual(text, "╔═ä☺⌂")  # 0x00 -> Leerzeichen (hinten abgeschnitten)

    def test_cli_text_is_ascii_json(self):
        box = start_prog("TEXTCP.COM")
        self.addCleanup(box.stop)
        wait_until(lambda: (r := self._row10(box)) and r.endswith("⌂") and r)
        proc = subprocess.run([sys.executable, "-m", "dosbox_mcp", "text", "--id", box.inst.id],
                              cwd=PYDIR, capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.isascii())
        self.assertEqual(json.loads(proc.stdout.decode("ascii"))["zeilen"][10], "╔═ä☺⌂")

    @staticmethod
    def _row10(box):
        try:
            row = box.screen_text()["zeilen"][10]
        except DmcpError:
            return None
        return row or None


if __name__ == "__main__":
    unittest.main()
