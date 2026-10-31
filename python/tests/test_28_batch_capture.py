import json
import shutil
import unittest
from pathlib import Path

from dosbox_lab import Dosbox, Mount
from tests.util import TESTPROGS, US, batch_workdir, need_build, run_typed, wait_prompt, wait_until


def start_batch(test, lines, start_arg="", extra=()):
    """Instance with a copy of BATCH.COM (and the programs from extra) as C:, autoexec START / lines / END;
    waits for done."""
    work = batch_workdir(test)
    for name in extra:
        shutil.copy(TESTPROGS / name, work / name)
    autoexec = [f"@Z:\\DMCPBAT.COM START {start_arg}".rstrip(), *lines, "@Z:\\DMCPBAT.COM END"]
    box = Dosbox.start(mounts=[Mount("C", str(work))], autoexec=autoexec, headless=True)
    test.addCleanup(box.stop)
    status = wait_until(lambda: (lambda s: s if s["done"] else None)(box.call("batch.status")), timeout=20)
    return box, work, status


@need_build
class BatchCaptureTest(unittest.TestCase):
    def test_program_output_exit_code_and_stop(self):
        box, work, status = start_batch(self, ["@CALL BATCH"])
        self.assertFalse(status["active"])
        self.assertNotIn("error", status)
        self.assertEqual(status["exit_code"], 7)
        self.assertEqual(status["programs"], 1)
        self.assertFalse(status["truncated"])
        self.assertEqual(Path(status["stdout_path"]).read_bytes(), b"ABTTY\r\n")  # TESTPROG-TEXT (BATCH.COM)
        self.assertEqual(Path(status["stderr_path"]).read_bytes(), b"ERR\r\n")  # TESTPROG-TEXT (BATCH.COM)
        self.assertEqual(status["stdout_bytes"], 7)
        self.assertEqual(status["stderr_bytes"], 5)
        self.assertEqual((work / "BATCH.OUT").read_bytes(), b"DMCP")
        self.assertEqual(status["last_exit_code"], 7)
        file = json.loads((Path(box.inst.outdir) / "batch.json").read_text(encoding="utf-8"))
        self.assertEqual(file, {"active": False, "done": True, "exit_code": 7, "programs": 1,
                                 "last_exit_code": 7, "stdout_bytes": 7, "stderr_bytes": 5,
                                 "truncated": False})
        emu = wait_until(lambda: (lambda s: s if s["paused"] else None)(box.status()), timeout=5)
        self.assertEqual(emu["stop_reason"], "batch")

    def test_shell_output_is_stdout(self):
        _box, _work, status = start_batch(self, ["@ECHO HELLO"])
        self.assertEqual(Path(status["stdout_path"]).read_bytes(), b"HELLO\r\n")
        self.assertEqual(status["programs"], 0)
        self.assertEqual(status["exit_code"], 0)
        self.assertNotIn("last_exit_code", status)  # DMCPBAT START itself does not count

    def test_limit_cuts_output(self):
        _box, _work, status = start_batch(self, ["@CALL BATCH"], start_arg="3")
        self.assertEqual(Path(status["stdout_path"]).read_bytes(), b"ABT")
        self.assertEqual(Path(status["stderr_path"]).read_bytes(), b"ERR")
        self.assertTrue(status["truncated"])
        self.assertEqual(status["exit_code"], 7)
        self.assertEqual(status["stdout_bytes"], 3)
        self.assertEqual(status["stderr_bytes"], 3)

    def test_redirect_keeps_full_stdout(self):
        _box, work, status = start_batch(self, ["@CALL BATCH > X.TXT"])
        self.assertEqual(Path(status["stdout_path"]).read_bytes(), b"ABTTY\r\n")  # capture is complete
        self.assertEqual((work / "X.TXT").read_bytes(), b"AB\r\n")  # DOS part; TTY goes through the BIOS
        self.assertEqual(Path(status["stderr_path"]).read_bytes(), b"ERR\r\n")
        self.assertEqual(status["stdout_bytes"], 7)
        self.assertEqual(status["exit_code"], 7)
        self.assertEqual(status["programs"], 1)

    def test_load_without_start_is_not_a_program(self):
        _box, _work, status = start_batch(self, ["@CALL LOADONLY"], extra=("LOADONLY.COM", "EXITCODE.COM"))
        self.assertEqual(status["programs"], 1)  # only LOADONLY; EXITCODE was merely loaded and does not count
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(Path(status["stdout_path"]).read_bytes(), b"")  # EXITCODE never ran

    def test_status_before_start(self):
        box = Dosbox.start(mounts=[Mount("C", str(batch_workdir(self)))], autoexec=[], headless=True)
        self.addCleanup(box.stop)
        status = box.call("batch.status")
        self.assertEqual((status["active"], status["done"], status["stdout_path"]), (False, False, ""))
        self.assertNotIn("exit_code", status)

    def test_open_failure_is_reported(self):
        box = Dosbox.start(mounts=[Mount("C", str(batch_workdir(self)))], autoexec=[], headless=True, conf=US)
        self.addCleanup(box.stop)
        wait_prompt(box)
        (Path(box.inst.outdir) / "stdout.txt").mkdir()  # fopen("stdout.txt", "wb") must fail
        run_typed(box, "Z:\\DMCPBAT.COM START")
        run_typed(box, "Z:\\DMCPBAT.COM END")
        status = wait_until(lambda: (lambda s: s if s["done"] else None)(box.call("batch.status")), timeout=20)
        self.assertIn("error", status)
        self.assertIn("stdout.txt", status["error"])


if __name__ == "__main__":
    unittest.main()
