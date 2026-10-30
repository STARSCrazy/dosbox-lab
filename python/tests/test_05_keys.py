import time
import unittest

from dosbox_mcp import DmcpError
from tests.util import class_box, need_build, start_prog, wait_until


@need_build
class KeysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Gast-Layout folgt standardmaessig dem Host (auto); der Test pinnt US, damit die Scancode-Erwartungen stabil sind.
        cls.box = class_box(cls, start_prog("KEYS.COM", conf={"dos": {"keyboardlayout": "us"}}))
        time.sleep(1.5)  # DOS-Start + Programmstart; KEYS.COM wartet danach in INT 16h


    def last_key(self):
        data = self.box.mem_read("l:4F0", 4)
        return int.from_bytes(data[:2], "little"), int.from_bytes(data[2:], "little")

    def test_single_key(self):
        _, count = self.last_key()
        self.box.key("A")
        self.box.wait_input()
        ax, new_count = wait_until(lambda: (k := self.last_key())[1] > count and k)
        self.assertEqual(ax >> 8, 0x1E)        # Scancode A
        self.assertEqual(ax & 0xFF, ord("a"))

    def test_shift_combo(self):
        _, count = self.last_key()
        self.box.key("SHIFT+B")
        self.box.wait_input()
        ax, _ = wait_until(lambda: (k := self.last_key())[1] > count and k)
        self.assertEqual(ax & 0xFF, ord("B"))

    def test_type_german(self):
        _, count = self.last_key()
        self.box.type_text("zY", layout="de")   # DE: z liegt auf der Y-Taste
        self.box.wait_input()
        ax, new_count = wait_until(lambda: (k := self.last_key())[1] >= count + 2 and k)
        self.assertEqual(ax >> 8, 0x2C)        # Scancode der US-Z-Taste (DE-Y)

    def test_type_crlf_is_one_enter(self):
        _, count = self.last_key()
        self.box.type_text("a\r\nb", layout="us")
        self.box.wait_input()
        ax, _ = wait_until(lambda: (k := self.last_key())[1] >= count + 3 and k)
        time.sleep(0.3)
        ax, new_count = self.last_key()
        self.assertEqual(new_count - count, 3)  # a, Enter, b - nicht a, Enter, Enter, b
        self.assertEqual(ax >> 8, 0x30)         # zuletzt b

    def test_own_false_releases_held_keys(self):
        self.addCleanup(self.box.own_input, True)
        self.addCleanup(self.box.release_all)
        self.box.key("A", ms=5000)
        wait_until(lambda: self.box.input_pending()["ausstehend"] == 1)  # Make-Code gesendet, Break wartet
        self.box.own_input(False)
        self.assertEqual(self.box.input_pending()["ausstehend"], 0)

    def test_untypable_char(self):
        _, count = self.last_key()
        with self.assertRaises(DmcpError) as ctx:
            self.box.type_text("a€b", layout="us")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("€", ctx.exception.message)
        self.assertEqual(self.box.input_pending()["ausstehend"], 0)
        self.assertEqual(self.last_key()[1], count)

    def test_unknown_key_name(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.key("HYPERSPACE")
        self.assertEqual(ctx.exception.code, "bad_argument")


if __name__ == "__main__":
    unittest.main()
