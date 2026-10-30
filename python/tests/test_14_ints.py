import unittest

from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt


def ah(event):
    return event["ax"][2:4]


@need_build
class IntsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        cls.box.events_start(typen=["int", "int_ret", "exit"])
        run_typed(cls.box, "INT21")
        cls.box.wait_event(lambda e: e["typ"] == "exit" and e["exit_code"] == 0x2B, timeout_s=20)
        cls.events = read_jsonl(cls.box.events_stop()["datei"])
        cls.ints = [e for e in cls.events if e["typ"] == "int"]


    def find(self, nummer, ah_value):
        return [e for e in self.ints if e["nummer"] == nummer and ah(e) == ah_value]

    def test_open_file_name(self):
        event = self.find("0x21", "3D")[0]
        self.assertEqual(event["dateiname"], "INT21.COM")
        self.assertEqual(event["modus"], "real")
        self.assertEqual(event["adr"]["modul"], "INT21.COM")

    def test_exec_and_dollar_strings(self):
        self.assertEqual(self.find("0x21", "4B")[0]["dateiname"], "EXITCODE.COM")
        texts = [e["text"] for e in self.find("0x21", "09")]
        self.assertIn("EXITCODE stdout\r\n", texts)
        self.assertIn("INT21 fertig\r\n", texts)

    def test_dpmi_alloc_with_result(self):
        alloc = next(e for e in self.ints if e["nummer"] == "0x31" and e["dpmi"]["funktion"] == "0x0000")
        self.assertEqual(alloc["dpmi"]["name"], "ldt_deskriptoren_anlegen")
        self.assertEqual(alloc["dpmi"]["eingabe"], {"anzahl": 1})
        ret = next(e for e in self.events if e["typ"] == "int_ret" and e["zu_nr"] == alloc["nr"])
        self.assertEqual(ret["ergebnis"], {"selektor": "0x00A7"})
        self.assertFalse(ret["cf"])
        self.assertEqual(ret["adr"]["modul"], "INT21.COM")

    def test_dpmi_set_base_decoded(self):
        event = next(e for e in self.ints if e["nummer"] == "0x31" and e["dpmi"]["funktion"] == "0x0007")
        self.assertEqual(event["dpmi"]["eingabe"], {"selektor": "0x00A7", "basis": "0x00123456"})

    def test_only_int31_returns_without_option(self):
        rets = [e for e in self.events if e["typ"] == "int_ret"]
        self.assertEqual(len(rets), 2)
        self.assertTrue(all(e["nummer"] == "0x31" for e in rets))

    def test_child_write_to_stderr(self):
        writes = self.find("0x21", "40")
        self.assertTrue(any(e["adr"].get("modul") == "EXITCODE.COM" and e["register"]["ebx"].endswith("0002")
                            for e in writes))

    def test_default_filter_and_no_callback_ints(self):
        self.assertEqual({e["nummer"] for e in self.ints} - {"0x21", "0x2F", "0x31", "0x33"}, set())
        self.assertEqual([e for e in self.ints if e["adr"].get("seg") == "0xF000"], [])
        self.assertTrue(all("weitergereicht_von" not in e for e in self.ints))  # kein PM im Spiel


@need_build
class IntReturnsTest(unittest.TestCase):
    def test_returns_for_all_logged_ints(self):
        box = start_idle()
        try:
            wait_prompt(box)
            box.events_start(typen=["int", "int_ret", "exit"], int_filter=[{"nummer": "21", "ah": "3D"}],
                             mit_rueckgabe=True)
            run_typed(box, "INT21")
            box.wait_event(lambda e: e["typ"] == "exit" and e["exit_code"] == 0x2B, timeout_s=20)
            events = read_jsonl(box.events_stop()["datei"])
            opens = [e for e in events if e["typ"] == "int"]
            self.assertEqual(len(opens), 1)
            ret = next(e for e in events if e["typ"] == "int_ret")
            self.assertEqual(ret["zu_nr"], opens[0]["nr"])
            self.assertFalse(ret["cf"])
            self.assertNotIn("ergebnis", ret)
        finally:
            box.stop()


if __name__ == "__main__":
    unittest.main()
