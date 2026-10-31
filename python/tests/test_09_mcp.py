import asyncio
import unittest

from tests.util import TESTPROGS, need_build


@need_build
class McpTest(unittest.TestCase):
    def test_tools_listed_and_callable(self):
        from dosbox_lab import mcp_server

        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        for expected in ("dosbox_start", "mem_read", "screenshot", "mouse_move", "key", "cpu_translate",
                         "events_start", "events_read", "calls_start", "calls_stop", "calls_status", "coverage_save", "flow_recent", "modules_list",
                         "modules_set_segments", "symbols_load", "symbol_lookup", "snapshot",
                         "emu_run_until", "emu_step_over", "emu_frame_step", "emu_eval", "break_list",
                         "break_int_add", "break_int_remove", "cpu_disassemble", "watch_add", "watch_remove",
                         "watch_list", "watch_log", "watch_clear_log", "state_save", "state_load",
                         "dos_programs", "dos_mcb", "dos_files", "dos_target", "mouse_move_program", "func_run"):
            self.assertIn(expected, names)
        started = mcp_server.dosbox_start(drive_c=str(TESTPROGS), commands=["MEMTEST.COM"], headless=True)
        try:
            regs = mcp_server.cpu_registers()
            self.assertEqual(regs["mode"], "real")
            out = mcp_server.mem_read("l:0", 16)
            self.assertEqual(len(out["hex"]), 32)
            mcp_server.coverage_start()
            info = mcp_server.coverage_save("mcp_cov")
            self.assertTrue(info["path"].endswith("mcp_cov.bin"))
            self.assertTrue(any(m["module"] == "MEMTEST.COM" for m in mcp_server.modules_list()["modules"]))
            snap = mcp_server.snapshot("mcp")
            self.assertTrue(snap["path"].endswith("_mcp"))
        finally:
            mcp_server.dosbox_stop(started["instance"])

    def test_debug_tools(self):
        from dosbox_lab import mcp_server
        started = mcp_server.dosbox_start(str(TESTPROGS), ["DEBUG.COM"], headless=True)
        inst = started["instance"]
        try:
            import time
            for _ in range(100):
                mods = mcp_server.modules_list(instance=inst)["modules"]
                if any(m["module"] == "DEBUG.COM" and "psp" in m for m in mods):
                    break
                time.sleep(0.1)
            st = mcp_server.emu_run_until(address="rel:DEBUG.COM:0000:010A", instance=inst)
            self.assertEqual(st["stop_reason"], "run_until")
            lines = mcp_server.cpu_disassemble(count=2, instance=inst)["lines"]
            self.assertEqual(lines[0]["bytes"], "A18001")
            self.assertEqual(int(mcp_server.emu_eval("ip", instance=inst)["value"], 16), 0x10A)
            self.assertTrue(mcp_server.dos_target(instance=inst)["found"])
        finally:
            mcp_server.dosbox_stop(instance=inst)


if __name__ == "__main__":
    unittest.main()
