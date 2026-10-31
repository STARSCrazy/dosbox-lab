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
                return hits["hits"]

            # MEMTEST starts asynchronously: wait for the marker instead of searching right away
            wait_until(search, timeout=15, interval=0.3)
            code, regs = cli("regs", "--id", inst)
            self.assertEqual(regs["mode"], "real")
            code, err = cli("read", "zz:1", "4", "--id", inst)
            self.assertEqual(code, 1)
            self.assertEqual(err["error"], "bad_argument")
            for bad in (("call", "emu.status", "{broken"), ("call", "emu.status", "[1, 2]"),
                        ("write", "l:4F0", "ZZ"), ("shot", "x", "--rect", "1,2")):
                code, err = cli(*bad, "--id", inst)
                self.assertEqual(code, 1, bad)
                self.assertEqual(err["error"], "bad_argument", bad)
            code, mods = cli("modules", "--id", inst)
            self.assertEqual(code, 0, mods)
            self.assertIn("MEMTEST.COM", [m["module"] for m in mods["modules"]])
            code, started_ev = cli("events-start", "--int", "21:3D", "--with-return", "--id", inst)
            self.assertEqual(code, 0, started_ev)
            self.assertTrue(started_ev["active"])
            code, read = cli("events-read", "--id", inst)
            self.assertEqual(code, 0, read)
            code, _ = cli("events-stop", "--id", inst)
            self.assertEqual(code, 0)
            code, cov = cli("cov-start", "--hits", "--id", inst)
            self.assertEqual(code, 0, cov)
            code, saved = cli("cov-save", "cli_cov", "--id", inst)
            self.assertEqual(code, 0, saved)
            self.assertIn("hits_path", saved)
            code, err = cli("events-start", "--int", "zz", "--id", inst)
            self.assertEqual((code, err["error"]), (1, "bad_argument"))
            code, listing = cli("list")
            self.assertIn(inst, [i["id"] for i in listing["instances"]])
        finally:
            cli("stop", "--id", inst)

    def test_calls_cli(self):
        code, started = cli("start", "--dir", str(TESTPROGS), "--cmd", "MEMTEST.COM", "--headless")
        self.assertEqual(code, 0, started)
        inst = started["id"]
        try:
            code, r = cli("calls-start", "--target", "l:00001000", "--range", "l:2000", "l:20FF", "--int", "21:3F",
                          "--stack", "16", "--max-per-target", "5", "--name", "calls_cli", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertTrue(r["active"])
            code, r = cli("calls-status", "--id", inst)
            self.assertTrue(r["active"])
            code, r = cli("calls-stop", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertFalse(r["active"])
            self.assertTrue(r["json_path"].endswith("calls_cli.json"))
            code, r = cli("events-start", "--preview", "8", "--type", "file", "--id", inst)
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
                return any(m["module"] == "DEBUG.COM" and "psp" in m for m in mods["modules"])

            wait_until(loaded, timeout=15, interval=0.3)
            code, st = cli("until", "--address", "rel:DEBUG.COM:0000:010A", "--id", inst)
            self.assertEqual(code, 0, st)
            code, dis = cli("dis", "--count", "2", "--id", inst)
            self.assertEqual(code, 0, dis)
            self.assertEqual(len(dis["lines"]), 2)
            code, ev = cli("eval", "ip", "--id", inst)
            self.assertEqual(code, 0, ev)
            code, tgt = cli("dos", "target", "--id", inst)
            self.assertEqual(code, 0, tgt)
            code, brk = cli("break", "rel:DEBUG.COM:0000:010D", "--id", inst)
            self.assertEqual(code, 0, brk)
            code, lst = cli("break-list", "--id", inst)
            self.assertEqual(code, 0, lst)
            code, rm = cli("unbreak", "--all", "--id", inst)
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
                return next((m for m in (mods or {}).get("modules", []) if m["module"] == "FUNC.COM" and "psp" in m),
                            None)
            psp = int(wait_until(func_module, timeout=20)["psp"], 16)
            base = psp * 16
            code, r = cli("calls-start", "--range", f"l:{base + 0x200:08X}", f"l:{base + 0x2FF:08X}",
                          "--states", "1", "--name", "fcli", "--id", inst)
            self.assertEqual(code, 0, r)
            code, r = cli("write", f"r:{psp:04X}:03F1", "01", "--id", inst)
            self.assertEqual(code, 0, r)
            wait_until(lambda: cli("read", f"r:{psp:04X}:03F0", "1", "--id", inst)[1].get("hex") == "01",
                       timeout=30, interval=0.2)
            code, r = cli("calls-stop", "--id", inst)
            self.assertEqual(code, 0, r)
            e = next(x for x in read_jsonl(r["file"]) if "state" in x and int(x["target"]["linear"], 16) == base + 0x200)
            code, r = cli("func-run", "--state", e["state"], "--write", f"l:{base + 0x200:08X}=B82A00C3",
                          "--reg", "ebx=0x5", "--id", inst)
            self.assertEqual(code, 0, r)
            self.assertEqual(r["end"], "ret")
            self.assertEqual(int(r["regs_out"]["eax"], 16) & 0xFFFF, 0x2A)
            self.assertEqual(int(r["regs_in"]["ebx"], 16), 5)
            code, r = cli("func-run", "--state", e["state"], "--reg", "ebx", "--id", inst)
            self.assertNotEqual(code, 0)
            self.assertEqual(r["error"], "bad_argument")
        finally:
            cli("stop", "--id", inst)


if __name__ == "__main__":
    unittest.main()
