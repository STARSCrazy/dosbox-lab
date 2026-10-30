import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from dosbox_mcp import DmcpError, Mount, run_batch
from dosbox_mcp.instance import Instance
from tests.util import TESTPROGS, batch_workdir, need_build


def write_bat(path, *lines):
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


@need_build
class RunBatchTest(unittest.TestCase):
    def test_program(self):
        work = batch_workdir(self)
        r = run_batch([Mount("C", str(work))], "BATCH", timeout_s=30)
        self.assertEqual(r["status"], "fertig")
        self.assertEqual(r["exit_code"], 7)
        self.assertEqual(r["programme"], 1)
        self.assertEqual(Path(r["stdout"]).read_bytes(), b"ABTTY\r\n")
        self.assertEqual(r["stdout_text"], "ABTTY\r\n")
        self.assertEqual(r["stderr_text"], "ERR\r\n")
        self.assertFalse(r["abgeschnitten"])
        self.assertNotIn("fehler", r)
        self.assertTrue(r["screen"].endswith("screen.txt"))
        self.assertIn("ABTTY", Path(r["screen"]).read_text(encoding="utf-8"))
        self.assertTrue(Path(r["log"]).exists())
        self.assertGreater(r["laufzeit_s"], 0)
        neu = [(d["laufwerk"], d["pfad"], d["groesse"], d["art"]) for d in r["dateien"]]
        self.assertEqual(neu, [("C", "BATCH.OUT", 4, "neu")])
        self.assertTrue(r["dateien_vollstaendig"])
        with self.assertRaises(DmcpError):
            Instance.attach(r["instanz"])  # Instanz ist beendet

    def test_batch_file_errorlevel_and_changed_file(self):
        work = batch_workdir(self)
        (work / "BATCH.OUT").write_bytes(b"alt")
        write_bat(work / "T.BAT", "@ECHO OFF", "BATCH", "ECHO NACH")
        r = run_batch([Mount("C", str(work))], "T", timeout_s=30)
        self.assertEqual(r["status"], "fertig")
        self.assertEqual(r["exit_code"], 7)
        self.assertEqual(r["stdout_text"], "ABTTY\r\nNACH\r\n")
        arten = {d["pfad"]: d["art"] for d in r["dateien"]}
        self.assertEqual(arten, {"BATCH.OUT": "geaendert"})

    def test_exit_ends_shell(self):
        work = batch_workdir(self)
        write_bat(work / "E.BAT", "@ECHO OFF", "BATCH", "EXIT")
        r = run_batch([Mount("C", str(work))], "E", timeout_s=60)
        self.assertEqual(r["status"], "shell_beendet")
        self.assertEqual(r["exit_code"], 7)  # Errorlevel des letzten Programms, EXIT aendert ihn nicht
        self.assertEqual(r["programme"], 1)
        self.assertEqual(r["stdout_text"], "ABTTY\r\n")
        self.assertIsNone(r["screen"])
        self.assertNotIn("rpc_fehler", r)
        self.assertLess(r["laufzeit_s"], 8)
        with self.assertRaises(DmcpError):
            Instance.attach(r["instanz"])  # Instanz ist beendet

    def test_exit_without_program(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "EXIT", timeout_s=60)
        self.assertEqual((r["status"], r["exit_code"], r["programme"]), ("shell_beendet", None, 0))
        self.assertLess(r["laufzeit_s"], 8)

    def test_unknown_command(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "GIBTSNIX", timeout_s=30)
        self.assertEqual(r["status"], "fertig")
        self.assertEqual(r["programme"], 0)
        self.assertEqual(r["exit_code"], 0)
        self.assertNotEqual(r["stdout_text"], "")  # Meldung der Shell
        self.assertEqual(r["dateien"], [])

    def test_output_limit(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "BATCH", timeout_s=30, max_ausgabe=3)
        self.assertEqual(r["status"], "fertig")
        self.assertTrue(r["abgeschnitten"])
        self.assertEqual(Path(r["stdout"]).read_bytes(), b"ABT")
        self.assertEqual(r["exit_code"], 7)

    def test_timeout_keeps_output_and_graphics_screen(self):
        work = batch_workdir(self)
        (work / "VIDEO.COM").write_bytes((TESTPROGS / "VIDEO.COM").read_bytes())
        write_bat(work / "H.BAT", "@ECHO OFF", "ECHO VOR", "VIDEO")
        r = run_batch([Mount("C", str(work))], "H", timeout_s=4)
        self.assertEqual(r["status"], "timeout")
        self.assertIsNone(r["exit_code"])
        self.assertEqual(r["stdout_text"], "VOR\r\n")
        self.assertTrue(r["screen"].endswith(".png"), r["screen"])
        self.assertLess(r["laufzeit_s"], 4 + 15)
        with self.assertRaises(DmcpError):
            Instance.attach(r["instanz"])

    def test_parallel_runs(self):
        works = [batch_workdir(self) for _ in range(3)]
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda w: run_batch([Mount("C", str(w))], "BATCH", timeout_s=60), works))
        self.assertEqual(len({r["instanz"] for r in results}), 3)
        for work, r in zip(works, results):
            self.assertEqual((r["status"], r["exit_code"], r["stdout_text"]), ("fertig", 7, "ABTTY\r\n"))
            self.assertEqual((work / "BATCH.OUT").read_bytes(), b"DMCP")

    def test_bad_arguments(self):
        mounts = [Mount("C", str(batch_workdir(self)))]
        for kwargs in ({"befehl": ""}, {"befehl": "  "}, {"befehl": "A\nB"}, {"befehl": "BATCH", "timeout_s": 0},
                       {"befehl": "BATCH", "max_ausgabe": 0}, {"befehl": "BATCH ä"},
                       {"befehl": "BATCH ─"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(DmcpError) as ctx:
                run_batch(mounts, **kwargs)
            self.assertEqual(ctx.exception.code, "bad_argument")
        with self.assertRaises(DmcpError) as ctx:
            run_batch([], "BATCH")
        self.assertEqual(ctx.exception.code, "bad_argument")


class RunBatchRpcErrorTest(unittest.TestCase):
    def test_rpc_error_while_alive_gives_timeout_result(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "out").mkdir()
        (base / "work").mkdir()
        stub = mock.Mock()
        stub.id = "stub0001"
        stub.outdir = base / "out"
        stub.rundir = base
        stub.client.call.side_effect = DmcpError("timeout", "emulation thread busy")
        stub.alive.return_value = True
        with mock.patch("dosbox_mcp.batch.Instance.start", return_value=stub):
            r = run_batch([Mount("C", str(base / "work"))], "X", timeout_s=1)
        self.assertEqual(r["status"], "timeout")
        self.assertIsNone(r["exit_code"])
        self.assertIn("timeout", r["rpc_fehler"])
        self.assertEqual(r["instanz"], "stub0001")
        stub.stop.assert_called_once()
        self.assertIsNone(r["stdout"])
        self.assertLess(r["laufzeit_s"], 1)
        stub.client.call.assert_called_once()  # nach rpc_fehler keine weiteren RPCs (Bildschirm, Status)
        self.assertIsNone(r["screen"])


if __name__ == "__main__":
    unittest.main()
