import unittest

from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt


@need_build
class ModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        cls.box.events_start(types=["mode", "new_code_selector"])
        run_typed(cls.box, "PMODE")
        cls.box.wait_event(lambda e: e["type"] == "mode" and e["to"]["paging"], timeout_s=20)
        cls.events = read_jsonl(cls.box.events_stop()["file"])
        cls.modes = [e for e in cls.events if e["type"] == "mode"]


    def test_first_event_is_start_state(self):
        first = self.modes[0]
        self.assertIsNone(first["from"])
        self.assertEqual(first["to"], {"mode": "real", "paging": False, "cr3": "0x00000000"})

    def test_switch_sequence(self):
        seq = [(e["to"]["mode"], e["to"]["paging"], e["to"]["cr3"]) for e in self.modes[1:]]
        self.assertEqual(seq, [("pm", False, "0x00000000"), ("pm", False, "0x00300000"),
                               ("pm", True, "0x00300000")])
        self.assertEqual(self.modes[1]["from"]["mode"], "real")

    def test_new_code_selector(self):
        # Right before PE is set, an instruction still runs with the old real-mode CS (value 0x0192 or similar);
        # it appears once without limit/dpl. What counts is the real selector 0008.
        news = [e for e in self.events if e["type"] == "new_code_selector"]
        sel8 = [e for e in news if e["sel"] == "0x0008"]
        self.assertEqual(len(sel8), 1)
        self.assertLessEqual(len(news), 2)
        event = sel8[0]
        self.assertEqual(event["sel"], "0x0008")
        self.assertEqual(event["descriptor"], {"base": "0x00000000", "limit": "0xFFFFFFFF", "bits": 32, "dpl": 0})
        self.assertEqual(event["addr"]["sel"], "0x0008")
        self.assertEqual(event["addr"]["mode"], "pm")


if __name__ == "__main__":
    unittest.main()
