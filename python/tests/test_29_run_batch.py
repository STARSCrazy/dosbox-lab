import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from dosbox_lab import DmcpError, Mount, run_batch
from dosbox_lab.instance import Instance
from tests.util import TESTPROGS, batch_workdir, need_build


def write_bat(path, *lines):
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))


@need_build
class RunBatchTest(unittest.TestCase):
    def test_program(self):
        work = batch_workdir(self)
        r = run_batch([Mount("C", str(work))], "BATCH", timeout_s=30)
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["exit_code"], 7)
        self.assertEqual(r["programs"], 1)
        # TESTPROG-TEXT (BATCH.COM prints ABTTY / ERR)
        self.assertEqual(Path(r["stdout"]).read_bytes(), b"ABTTY\r\n")
        self.assertEqual(r["stdout_text"], "ABTTY\r\n")
        self.assertEqual(r["stderr_text"], "ERR\r\n")
        self.assertFalse(r["truncated"])
        self.assertNotIn("error", r)
        self.assertTrue(r["screen"].endswith("screen.txt"))
        self.assertIn("ABTTY", Path(r["screen"]).read_text(encoding="utf-8"))
        self.assertTrue(Path(r["log"]).exists())
        self.assertGreater(r["runtime_s"], 0)
        new = [(d["drive"], d["path"], d["size"], d["kind"]) for d in r["files"]]
        self.assertEqual(new, [("C", "BATCH.OUT", 4, "new")])
        self.assertTrue(r["files_complete"])
        with self.assertRaises(DmcpError):
            Instance.attach(r["instance"])  # Instance is exited

    def test_batch_file_errorlevel_and_changed_file(self):
        work = batch_workdir(self)
        (work / "BATCH.OUT").write_bytes(b"old")
        write_bat(work / "T.BAT", "@ECHO OFF", "BATCH", "ECHO AFTER")
        r = run_batch([Mount("C", str(work))], "T", timeout_s=30)
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["exit_code"], 7)
        self.assertEqual(r["stdout_text"], "ABTTY\r\nAFTER\r\n")
        kinds = {d["path"]: d["kind"] for d in r["files"]}
        self.assertEqual(kinds, {"BATCH.OUT": "changed"})

    def test_exit_ends_shell(self):
        work = batch_workdir(self)
        write_bat(work / "E.BAT", "@ECHO OFF", "BATCH", "EXIT")
        r = run_batch([Mount("C", str(work))], "E", timeout_s=60)
        self.assertEqual(r["status"], "shell_exited")
        self.assertEqual(r["exit_code"], 7)  # errorlevel of the last program, EXIT does not change it
        self.assertEqual(r["programs"], 1)
        self.assertEqual(r["stdout_text"], "ABTTY\r\n")
        self.assertIsNone(r["screen"])
        self.assertNotIn("rpc_error", r)
        self.assertLess(r["runtime_s"], 8)
        with self.assertRaises(DmcpError):
            Instance.attach(r["instance"])  # Instance is exited

    def test_exit_without_program(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "EXIT", timeout_s=60)
        self.assertEqual((r["status"], r["exit_code"], r["programs"]), ("shell_exited", None, 0))
        self.assertLess(r["runtime_s"], 8)

    def test_unknown_command(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "NOSUCHCMD", timeout_s=30)
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["programs"], 0)
        self.assertEqual(r["exit_code"], 0)
        self.assertNotEqual(r["stdout_text"], "")  # message of the shell
        self.assertEqual(r["files"], [])

    def test_output_limit(self):
        r = run_batch([Mount("C", str(batch_workdir(self)))], "BATCH", timeout_s=30, max_output=3)
        self.assertEqual(r["status"], "done")
        self.assertTrue(r["truncated"])
        self.assertEqual(Path(r["stdout"]).read_bytes(), b"ABT")
        self.assertEqual(r["exit_code"], 7)

    def test_timeout_keeps_output_and_graphics_screen(self):
        work = batch_workdir(self)
        (work / "VIDEO.COM").write_bytes((TESTPROGS / "VIDEO.COM").read_bytes())
        write_bat(work / "H.BAT", "@ECHO OFF", "ECHO BEFORE", "VIDEO")
        r = run_batch([Mount("C", str(work))], "H", timeout_s=4)
        self.assertEqual(r["status"], "timeout")
        self.assertIsNone(r["exit_code"])
        self.assertEqual(r["stdout_text"], "BEFORE\r\n")
        self.assertTrue(r["screen"].endswith(".png"), r["screen"])
        self.assertLess(r["runtime_s"], 4 + 15)
        with self.assertRaises(DmcpError):
            Instance.attach(r["instance"])

    def test_parallel_runs(self):
        works = [batch_workdir(self) for _ in range(3)]
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda w: run_batch([Mount("C", str(w))], "BATCH", timeout_s=60), works))
        self.assertEqual(len({r["instance"] for r in results}), 3)
        for work, r in zip(works, results):
            self.assertEqual((r["status"], r["exit_code"], r["stdout_text"]), ("done", 7, "ABTTY\r\n"))
            self.assertEqual((work / "BATCH.OUT").read_bytes(), b"DMCP")

    def test_bad_arguments(self):
        mounts = [Mount("C", str(batch_workdir(self)))]
        for kwargs in ({"command": ""}, {"command": "  "}, {"command": "A\nB"}, {"command": "BATCH", "timeout_s": 0},
                       {"command": "BATCH", "max_output": 0}, {"command": "BATCH ä"},
                       {"command": "BATCH ─"}):
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
        with mock.patch("dosbox_lab.batch.Instance.start", return_value=stub):
            r = run_batch([Mount("C", str(base / "work"))], "X", timeout_s=1)
        self.assertEqual(r["status"], "timeout")
        self.assertIsNone(r["exit_code"])
        self.assertIn("timeout", r["rpc_error"])
        self.assertEqual(r["instance"], "stub0001")
        stub.stop.assert_called_once()
        self.assertIsNone(r["stdout"])
        self.assertLess(r["runtime_s"], 1)
        stub.client.call.assert_called_once()  # after rpc_error no further RPCs (screen, status)
        self.assertIsNone(r["screen"])


if __name__ == "__main__":
    unittest.main()
