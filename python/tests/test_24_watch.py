import time
import unittest

from dosbox_lab import DmcpError
from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, prog_psp, run_typed, start_idle, start_prog, wait_prompt, wait_until


class DebugProg:
    def adr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def wert(self):
        return int.from_bytes(self.box.mem_read(self.adr(0x180), 2), "little")


@need_build
class WatchTest(unittest.TestCase, DebugProg):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def tearDown(self):
        self.box.watch_remove(all=True)
        self.box.watch_clear_log()
        if self.box.status()["paused"]:
            self.box.resume()

    def test_write_watch_stops_after_write(self):
        info = self.box.watch_add(self.adr(0x180), laenge=2, art="schreiben")
        self.assertEqual(int(info["start"], 16), self.psp * 16 + 0x180)
        st = self.box.wait_paused()
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        hit = st["stop_watch"]
        self.assertEqual(hit["art"], "schreiben")
        self.assertEqual(hit["breite"], 2)
        self.assertEqual(int(hit["adr"]["off"], 16), 0x10E)
        self.assertEqual(int(hit["ziel"]["linear"], 16), self.psp * 16 + 0x180)
        self.assertEqual(int(hit["wert"], 16), (int(hit["alt"], 16) + 1) & 0xFFFF)
        self.assertEqual(int(hit["wert"], 16), self.wert())
        self.assertEqual(self.box.watch_list()["waechter"][0]["treffer"], 1)

    def test_breakpoint_right_after_watch_stop(self):
        # Waechter haelt vor 0111; ein Haltepunkt genau dort darf beim Weiterlaufen nicht uebersprungen werden.
        self.box.watch_add(self.adr(0x180), laenge=2, art="schreiben")
        self.box.break_add(self.adr(0x111))
        try:
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "watch")
            self.assertEqual(int(st["eip"], 16), 0x111)
            before = self.wert()
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(int(st["eip"], 16), 0x111)
            self.assertEqual(self.wert(), before)
        finally:
            self.box.break_remove(all=True)

    def test_read_watch(self):
        self.box.watch_add(self.adr(0x180), laenge=2, art="lesen")
        st = self.box.wait_paused()
        self.assertEqual(int(st["eip"], 16), 0x10D)
        self.assertEqual(st["stop_watch"]["art"], "lesen")
        self.assertEqual(int(st["stop_watch"]["adr"]["off"], 16), 0x10A)
        self.assertNotIn("alt", st["stop_watch"])

    def test_continue_stops_again(self):
        self.box.watch_add(self.adr(0x180), laenge=2)
        first = int(self.box.wait_paused()["stop_watch"]["wert"], 16)
        self.box.resume()
        second = int(self.box.wait_paused()["stop_watch"]["wert"], 16)
        self.assertEqual(second, (first + 1) & 0xFFFF)

    def test_step_stops_at_watch(self):
        self.box.run_until(adresse=self.adr(0x10A))
        self.box.watch_add(self.adr(0x180), laenge=2)
        st = self.box.step(200)
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        self.assertLess(st["steps_done"], 200)
        self.assertEqual(int(st["stop_watch"]["adr"]["off"], 16), 0x10E)

    def test_step_over_plain_instruction_stops_at_watch(self):
        self.box.run_until(adresse=self.adr(0x10E))
        self.box.watch_add(self.adr(0x180), laenge=2)
        st = self.box.step_over()
        self.assertEqual(st["art"], "schritt")
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        self.assertEqual(int(st["stop_watch"]["adr"]["off"], 16), 0x10E)

    def test_log_mode(self):
        self.box.watch_add(self.adr(0x180), laenge=2, art="beides", aktion="protokollieren")
        time.sleep(1.0)
        self.assertFalse(self.box.status()["paused"])
        result = self.box.watch_log(name="zugriffe_debug")
        entries = read_jsonl(result["pfad"])
        self.assertGreater(len(entries), 4)
        self.assertEqual(result["anzahl"], len(entries))
        offs = {(e["art"], int(e["adr"]["off"], 16)) for e in entries}
        self.assertEqual(offs, {("lesen", 0x10A), ("schreiben", 0x10E)})
        self.assertTrue(entries[0]["wert"].startswith("0x"))
        for read, write in zip(entries, entries[1:]):
            if read["art"] == "lesen" and write["art"] == "schreiben":
                self.assertEqual(int(write["wert"], 16), (int(read["wert"], 16) + 1) & 0xFFFF)
        nrs = [e["nr"] for e in entries]
        self.assertEqual(nrs, sorted(nrs))

    def test_no_code_fetch_hits(self):
        # unter (0140..0144) laeuft jede Runde: Befehlsholen ist kein Lesezugriff.
        self.box.watch_add(self.adr(0x140), laenge=5, art="lesen")
        time.sleep(1.0)
        self.assertFalse(self.box.status()["paused"])
        self.assertEqual(self.box.watch_list()["waechter"][0]["treffer"], 0)

    def test_physical_address_without_paging(self):
        info = self.box.watch_add(f"p:{self.psp * 16 + 0x182:X}", laenge=2)
        self.assertTrue(info["hinweise"])
        st = self.box.wait_paused()
        self.assertEqual(int(st["stop_watch"]["adr"]["off"], 16), 0x140)

    def test_errors(self):
        for kwargs in ({"laenge": 0}, {"laenge": 2_000_000}, {"art": "x"}, {"aktion": "x"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.watch_add(self.adr(0x180), **kwargs)
                self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertFalse(self.box.watch_remove(9999)["entfernt"])

    def test_remove_restores_speed_path(self):
        info = self.box.watch_add(self.adr(0x180), laenge=2, aktion="protokollieren")
        self.box.watch_remove(info["id"])
        time.sleep(0.3)
        self.assertEqual(self.box.watch_list()["waechter"], [])
        before = self.wert()
        wait_until(lambda: self.wert() != before, timeout=5)


@need_build
class WatchWithRecordingTest(unittest.TestCase, DebugProg):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def test_stops_with_recording(self):
        self.box.flow_start(ring_groesse=100000)
        self.box.coverage_start()
        self.box.watch_add(self.adr(0x180), laenge=2)
        values = []
        for _ in range(3):
            values.append(int(self.box.wait_paused()["stop_watch"]["wert"], 16))
            self.box.resume()
        self.box.watch_remove(all=True)
        self.assertEqual(values, [values[0], (values[0] + 1) & 0xFFFF, (values[0] + 2) & 0xFFFF])
        recent = read_jsonl(self.box.flow_recent(100000, "rec_watch")["pfad"])
        self.assertFalse([e for e in recent if e["quelle"]["linear"] == e["ziel"]["linear"]
                          and e["art"] not in ("irq", "exception", "int")])
        self.box.flow_stop()
        self.box.coverage_stop()


@need_build
class WatchPmodeTest(unittest.TestCase):
    """Waechter vor dem Wechsel in PM mit Paging anlegen; er muss nach dem TLB-Neuaufbau ausloesen."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)


    def test_watch_survives_mode_switch(self):
        info = self.box.watch_add("l:01000004", laenge=4)
        self.assertTrue(info["hinweise"])  # Seite im Real Mode nicht eingeblendet
        run_typed(self.box, "PMODE")
        st = self.box.wait_paused(timeout_s=15)
        self.assertEqual(st["stop_reason"], "watch")
        self.assertTrue(st["pmode"])
        hit = st["stop_watch"]
        self.assertEqual(int(hit["ziel"]["linear"], 16), 0x01000004)
        self.assertEqual(hit["breite"], 4)
        first = int(hit["wert"], 16)
        self.box.resume()
        st = self.box.wait_paused()
        self.assertEqual(int(st["stop_watch"]["wert"], 16), first + 1)
        self.assertEqual(int(st["stop_watch"]["alt"], 16), first)
