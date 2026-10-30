import unittest
from pathlib import Path

from dosbox_mcp import DmcpError
from dosbox_mcp.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt, wait_until


@need_build
class EventsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        started = cls.box.events_start()
        assert started["aktiv"], started
        run_typed(cls.box, "INT21")
        cls.box.wait_event(lambda e: e["typ"] == "exit" and e["exit_code"] == 0x2B, timeout_s=20)
        cls.read_batch = cls.box.events_read(seit_nr=0, max=500)
        cls.status = cls.box.events_stop()
        cls.events = read_jsonl(cls.status["datei"])


    def test_exec_events(self):
        execs = [e for e in self.events if e["typ"] == "exec"]
        self.assertEqual([e["modul"] for e in execs], ["INT21.COM", "EXITCODE.COM"])
        child = execs[1]
        self.assertEqual(child["datei"], "C:\\EXITCODE.COM")
        self.assertEqual(child["befehlszeile"], " ARG1")
        self.assertEqual(child["art"], "com")
        self.assertEqual(child["al"], "0x00")
        psp = int(child["psp"], 16)
        self.assertEqual(child["rel_basis"], child["psp"])
        self.assertEqual(int(child["load_seg"], 16), psp + 0x10)
        self.assertEqual(child["einsprung"], f"{psp:04X}:0100")

    def test_exit_codes(self):
        exits = [(e["modul"], e["exit_code"], e["art"]) for e in self.events if e["typ"] == "exit"]
        self.assertEqual(exits, [("EXITCODE.COM", 0x2A, "normal"), ("INT21.COM", 0x2B, "normal")])

    def test_numbering_and_common_fields(self):
        nrs = [e["nr"] for e in self.events]
        self.assertEqual(nrs, sorted(set(nrs)))
        for e in self.events:
            self.assertIsInstance(e["zeit_ms"], float)
            self.assertTrue(e["adr"]["linear"].startswith("0x"), e)

    def test_read_matches_file(self):
        in_file = [e["nr"] for e in self.events if e["nr"] <= self.read_batch["letzte_nr"]]
        self.assertEqual([e["nr"] for e in self.read_batch["ereignisse"]], in_file[:500])
        self.assertFalse(self.read_batch["verpasst"])

    def test_status_after_stop(self):
        self.assertFalse(self.status["aktiv"])
        self.assertEqual(self.status["anzahl"], len(self.events))
        self.assertEqual(self.status["bytes"], Path(self.status["datei"]).stat().st_size)


@need_build
class EventsLimitTest(unittest.TestCase):
    def test_limit_stops_cleanly(self):
        box = start_idle()
        try:
            wait_prompt(box)
            box.events_start(max_bytes=4096)
            status = box.events_status()
            for _ in range(6):  # a run writes ~950 bytes (exec/exit) or more (with int events)
                if status["limit_erreicht"]:
                    break
                run_typed(box, "INT21")
                prev = status["letzte_nr"]

                def run_over():
                    s = box.events_status()
                    seen = box.events_read(seit_nr=prev, max=500)["ereignisse"]
                    done = any(e["typ"] == "exit" and e["modul"] == "INT21.COM" for e in seen)
                    return s if s["limit_erreicht"] or done else None

                status = wait_until(run_over, timeout=20)
            self.assertTrue(status["limit_erreicht"], status)
            self.assertFalse(status["aktiv"])
            raw = Path(status["datei"]).read_bytes()
            last_line = raw.rstrip(b"\n").split(b"\n")[-1]
            self.assertLessEqual(len(raw) - len(last_line) - 1, 4096)
            self.assertEqual(read_jsonl(status["datei"])[-1]["typ"], "limit_reached")
            wait_until(lambda: "INT21 fertig" in [l.strip() for l in box.screen_text()["zeilen"]], timeout=15)
        finally:
            box.stop()

    def test_bad_arguments_and_idle_calls(self):
        box = start_idle()
        try:
            batch = box.events_read()
            self.assertEqual(batch["ereignisse"], [])
            self.assertFalse(batch["aktiv"])
            box.events_stop()
            for bad in ({"typen": ["gibtsnicht"]}, {"max_bytes": 10}, {"int_modus": "v86"},
                        {"int_filter": [{"ah": "3D"}]}):
                with self.assertRaises(DmcpError, msg=bad) as ctx:
                    box.call("events.start", **bad)
                self.assertEqual(ctx.exception.code, "bad_argument", bad)
        finally:
            box.stop()


if __name__ == "__main__":
    unittest.main()
