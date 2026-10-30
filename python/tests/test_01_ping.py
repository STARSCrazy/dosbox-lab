import unittest

from dosbox_mcp import DmcpError
from tests.util import need_build, start_prog


@need_build
class PingTest(unittest.TestCase):
    def test_ping_twice(self):
        box = start_prog("MEMTEST.COM")
        try:
            self.assertEqual(box.ping()["protocol"], "dmcp/1")
            self.assertTrue(box.call("dmcp.ping_emu")["pong"])
            self.assertIn("dmcp.ping", box.call("dmcp.methods")["methods"])
        finally:
            box.stop()

    def test_unknown_method_is_rpc_error(self):
        box = start_prog("MEMTEST.COM")
        try:
            for method in ("dmcp.gibtsnicht", "emu.gibtsnicht", "cpu.x", "mem.x", "input.x", "screen.x"):
                with self.assertRaises(DmcpError) as ctx:
                    box.call(method)
                self.assertEqual(ctx.exception.code, "bad_argument", method)
        finally:
            box.stop()

    def test_dead_instance(self):
        box = start_prog("MEMTEST.COM")
        box.inst._proc.kill()
        box.inst._proc.wait(5)
        with self.assertRaises(DmcpError) as ctx:
            box.ping()
        self.assertEqual(ctx.exception.code, "instance_dead")


if __name__ == "__main__":
    unittest.main()
