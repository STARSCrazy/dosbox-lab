import unittest

from dosbox_mcp.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt


@need_build
class ModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        cls.box.events_start(typen=["mode", "new_code_selector"])
        run_typed(cls.box, "PMODE")
        cls.box.wait_event(lambda e: e["typ"] == "mode" and e["nach"]["paging"], timeout_s=20)
        cls.events = read_jsonl(cls.box.events_stop()["datei"])
        cls.modes = [e for e in cls.events if e["typ"] == "mode"]


    def test_first_event_is_start_state(self):
        first = self.modes[0]
        self.assertIsNone(first["von"])
        self.assertEqual(first["nach"], {"modus": "real", "paging": False, "cr3": "0x00000000"})

    def test_switch_sequence(self):
        seq = [(e["nach"]["modus"], e["nach"]["paging"], e["nach"]["cr3"]) for e in self.modes[1:]]
        self.assertEqual(seq, [("pm", False, "0x00000000"), ("pm", False, "0x00300000"),
                               ("pm", True, "0x00300000")])
        self.assertEqual(self.modes[1]["von"]["modus"], "real")

    def test_new_code_selector(self):
        # Direkt nach dem Setzen von PE laeuft ein Befehl mit dem alten Real-Mode-CS (Wert 0x0192 o. ae.);
        # dieser erscheint einmal ohne limit/dpl. Massgeblich ist der echte Selektor 0008.
        news = [e for e in self.events if e["typ"] == "new_code_selector"]
        sel8 = [e for e in news if e["sel"] == "0x0008"]
        self.assertEqual(len(sel8), 1)
        self.assertLessEqual(len(news), 2)
        event = sel8[0]
        self.assertEqual(event["sel"], "0x0008")
        self.assertEqual(event["deskriptor"], {"basis": "0x00000000", "limit": "0xFFFFFFFF", "bits": 32, "dpl": 0})
        self.assertEqual(event["adr"]["sel"], "0x0008")
        self.assertEqual(event["adr"]["modus"], "pm")


if __name__ == "__main__":
    unittest.main()
