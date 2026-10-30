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
        started = mcp_server.dosbox_start(laufwerk_c=str(TESTPROGS), befehle=["MEMTEST.COM"], headless=True)
        try:
            regs = mcp_server.cpu_registers()
            self.assertEqual(regs["modus"], "real")
            out = mcp_server.mem_read("l:0", 16)
            self.assertEqual(len(out["hex"]), 32)
            mcp_server.coverage_start()
            info = mcp_server.coverage_save("mcp_cov")
            self.assertTrue(info["pfad"].endswith("mcp_cov.bin"))
            self.assertTrue(any(m["modul"] == "MEMTEST.COM" for m in mcp_server.modules_list()["module"]))
            snap = mcp_server.snapshot("mcp")
            self.assertTrue(snap["pfad"].endswith("_mcp"))
        finally:
            mcp_server.dosbox_stop(started["instanz"])

    def test_debug_tools(self):
        from dosbox_lab import mcp_server
        started = mcp_server.dosbox_start(str(TESTPROGS), ["DEBUG.COM"], headless=True)
        inst = started["instanz"]
        try:
            import time
            for _ in range(100):
                mods = mcp_server.modules_list(instanz=inst)["module"]
                if any(m["modul"] == "DEBUG.COM" and "psp" in m for m in mods):
                    break
                time.sleep(0.1)
            st = mcp_server.emu_run_until(adresse="rel:DEBUG.COM:0000:010A", instanz=inst)
            self.assertEqual(st["stop_reason"], "run_until")
            lines = mcp_server.cpu_disassemble(anzahl=2, instanz=inst)["zeilen"]
            self.assertEqual(lines[0]["bytes"], "A18001")
            self.assertEqual(int(mcp_server.emu_eval("ip", instanz=inst)["wert"], 16), 0x10A)
            self.assertTrue(mcp_server.dos_target(instanz=inst)["gefunden"])
        finally:
            mcp_server.dosbox_stop(instanz=inst)


if __name__ == "__main__":
    unittest.main()
