import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.util import TESTPROGS, need_build, wait_until

PYDIR = Path(__file__).resolve().parents[1]


def cli(*args):
    proc = subprocess.run([sys.executable, "-m", "dosbox_lab", *args], cwd=PYDIR,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip() else None


@need_build
class CliTest(unittest.TestCase):
    def test_roundtrip(self):
        code, started = cli("start", "--dir", str(TESTPROGS), "--cmd", "MEMTEST.COM", "--headless")
        self.assertEqual(code, 0, started)
        inst = started["id"]
        try:
            def search():
                code, hits = cli("search", "l:0", "A0000", "--text", "DMCP-MEMTEST-MARK", "--id", inst)
                self.assertEqual(code, 0)
                return hits["treffer"]

            # MEMTEST laeuft asynchron an: auf den Marker warten statt sofort zu suchen
            wait_until(search, timeout=15, interval=0.3)
            code, regs = cli("regs", "--id", inst)
            self.assertEqual(regs["modus"], "real")
            code, err = cli("read", "zz:1", "4", "--id", inst)
            self.assertEqual(code, 1)
            self.assertEqual(err["fehler"], "bad_argument")
            for bad in (("call", "emu.status", "{kaputt"), ("call", "emu.status", "[1, 2]"),
                        ("write", "l:4F0", "ZZ"), ("shot", "x", "--rect", "1,2")):
                code, err = cli(*bad, "--id", inst)
                self.assertEqual(code, 1, bad)
                self.assertEqual(err["fehler"], "bad_argument", bad)
            code, mods = cli("modules", "--id", inst)
            self.assertEqual(code, 0, mods)
            self.assertIn("MEMTEST.COM", [m["modul"] for m in mods["module"]])
            code, started_ev = cli("events-start", "--int", "21:3D", "--rueckgabe", "--id", inst)
            self.assertEqual(code, 0, started_ev)
            self.assertTrue(started_ev["aktiv"])
            code, read = cli("events-read", "--id", inst)
            self.assertEqual(code, 0, read)
            code, _ = cli("events-stop", "--id", inst)
            self.assertEqual(code, 0)
            code, cov = cli("cov-start", "--hits", "--id", inst)
            self.assertEqual(code, 0, cov)
            code, saved = cli("cov-save", "cli_cov", "--id", inst)
            self.assertEqual(code, 0, saved)
            self.assertIn("hits_pfad", saved)
            code, err = cli("events-start", "--int", "zz", "--id", inst)
            self.assertEqual((code, err["fehler"]), (1, "bad_argument"))
            code, listing = cli("list")
            self.assertIn(inst, [i["id"] for i in listing["instanzen"]])
        finally:
            cli("stop", "--id", inst)

    def test_calls_cli(self):
        code, started = cli("start", "--dir", str(TESTPROGS), "--cmd", "MEMTEST.COM", "--headless")
        self.assertEqual(code, 0, started)
        inst = started["id"]
        try:
            code, r = cli("calls-start", "--ziel", "l:00001000", "--bereich", "l:2000", "l:20FF", "--int", "21:3F",
                          "--stack", "16", "--max-je-ziel", "5", "--name", "calls_cli", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertTrue(r["aktiv"])
            code, r = cli("calls-status", "--id", inst)
            self.assertTrue(r["aktiv"])
            code, r = cli("calls-stop", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertFalse(r["aktiv"])
            self.assertTrue(r["json_pfad"].endswith("calls_cli.json"))
            code, r = cli("events-start", "--vorschau", "8", "--typ", "datei", "--id", inst)
            self.assertEqual(code, 0, r)
            cli("events-stop", "--id", inst)
        finally:
            cli("stop", "--id", inst)

    def test_debug_commands(self):
        code, started = cli("start", "--dir", str(TESTPROGS), "--cmd", "DEBUG.COM", "--headless")
        self.assertEqual(code, 0, started)
        inst = started["id"]
        try:
            def loaded():
                code, mods = cli("modules", "--id", inst)
                return any(m["modul"] == "DEBUG.COM" and "psp" in m for m in mods["module"])

            wait_until(loaded, timeout=15, interval=0.3)
            code, st = cli("until", "--adresse", "rel:DEBUG.COM:0000:010A", "--id", inst)
            self.assertEqual(code, 0, st)
            code, dis = cli("dis", "--anzahl", "2", "--id", inst)
            self.assertEqual(code, 0, dis)
            self.assertEqual(len(dis["zeilen"]), 2)
            code, ev = cli("eval", "ip", "--id", inst)
            self.assertEqual(code, 0, ev)
            code, tgt = cli("dos", "target", "--id", inst)
            self.assertEqual(code, 0, tgt)
            code, brk = cli("break", "rel:DEBUG.COM:0000:010D", "--id", inst)
            self.assertEqual(code, 0, brk)
            code, lst = cli("break-list", "--id", inst)
            self.assertEqual(code, 0, lst)
            code, rm = cli("unbreak", "--alle", "--id", inst)
            self.assertEqual(code, 0, rm)
        finally:
            cli("stop", "--id", inst)

    def test_func_cli(self):
        from dosbox_lab.files import read_jsonl
        code, started = cli("start", "--dir", str(TESTPROGS), "--cmd", "FUNC.COM", "--headless")
        self.assertEqual(code, 0, started)
        inst = started["id"]
        try:
            def func_module():
                _, mods = cli("modules", "--id", inst)
                return next((m for m in (mods or {}).get("module", []) if m["modul"] == "FUNC.COM" and "psp" in m),
                            None)
            psp = int(wait_until(func_module, timeout=20)["psp"], 16)
            base = psp * 16
            code, r = cli("calls-start", "--bereich", f"l:{base + 0x200:08X}", f"l:{base + 0x2FF:08X}",
                          "--zustaende", "1", "--name", "fcli", "--id", inst)
            self.assertEqual(code, 0, r)
            code, r = cli("write", f"r:{psp:04X}:03F1", "01", "--id", inst)
            self.assertEqual(code, 0, r)
            wait_until(lambda: cli("read", f"r:{psp:04X}:03F0", "1", "--id", inst)[1].get("hex") == "01",
                       timeout=30, interval=0.2)
            code, r = cli("calls-stop", "--id", inst)
            self.assertEqual(code, 0, r)
            e = next(x for x in read_jsonl(r["datei"]) if "zustand" in x and int(x["ziel"]["linear"], 16) == base + 0x200)
            code, r = cli("func-run", "--zustand", e["zustand"], "--schreiben", f"l:{base + 0x200:08X}=B82A00C3",
                          "--reg", "ebx=0x5", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertEqual(r["ende"], "ret")
            self.assertEqual(int(r["regs_aus"]["eax"], 16) & 0xFFFF, 0x2A)
            self.assertEqual(int(r["regs_ein"]["ebx"], 16), 5)
            code, r = cli("func-run", "--zustand", e["zustand"], "--reg", "ebx", "--id", inst)
            self.assertNotEqual(code, 0)
            self.assertEqual(r["fehler"], "bad_argument")
        finally:
            cli("stop", "--id", inst)


if __name__ == "__main__":
    unittest.main()
