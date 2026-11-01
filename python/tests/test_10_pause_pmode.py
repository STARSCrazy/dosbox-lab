import time
import unittest

from dosbox_lab import DmcpError
from dosbox_lab.instance import TEXT_ENCODING
from tests.util import need_build, run_typed, start_idle, wait_until


@need_build
class PausePmodeTest(unittest.TestCase):
    def setUp(self):
        self.box = start_idle()

    def tearDown(self):
        self.box.stop()

    def test_pause_until_pmode(self):
        status = self.box.call("emu.pause", until="pmode")
        self.assertFalse(status["paused"])
        self.assertEqual(status["waiting_for"], "pmode")
        run_typed(self.box, "PMODE")
        status = self.box.wait_paused(15)
        self.assertEqual(status["stop_reason"], "pmode")
        self.assertTrue(status["pmode"])
        self.assertNotIn("waiting_for", status)
        self.assertEqual(self.box.translate("0010:00000000")["mode"], "pm")
        # The first PM instruction still runs with the old real-mode CS; only the far jump loads 0008.
        self.assertEqual(self.box.step(1)["cs"], "0008")

    def test_already_in_pm_pauses_at_once(self):
        run_typed(self.box, "PMODE")
        wait_until(lambda: self.box.status()["pmode"], timeout=15)
        status = self.box.pause(until="pmode")
        self.assertTrue(status["paused"])
        self.assertEqual(status["stop_reason"], "pmode")

    def test_plain_pause_cancels_wait(self):
        self.box.call("emu.pause", until="pmode")
        status = self.box.pause()
        self.assertTrue(status["paused"])
        self.assertNotIn("waiting_for", status)
        self.box.resume()

    def test_pause_timeout_disarms_condition(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.pause(until="pmode", timeout_s=0.5)  # DOS prompt never reaches PM
        self.assertEqual(ctx.exception.code, "timeout")
        status = self.box.status()
        self.assertNotIn("waiting_for", status)
        self.assertFalse(status["paused"])

    def test_bad_condition(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("emu.pause", until="paging")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_status_time_and_ram(self):
        first = self.box.status()["time_ms"]
        time.sleep(0.3)
        self.assertGreater(self.box.status()["time_ms"], first)
        self.assertEqual(self.box.status()["ram_bytes"], 16 * 1024 * 1024)

    def test_wheel_key_disabled(self):
        conf = (self.box.inst.rundir / "dosbox.conf").read_text(encoding=TEXT_ENCODING)
        self.assertIn("mouse_wheel_key=0", conf)


if __name__ == "__main__":
    unittest.main()
