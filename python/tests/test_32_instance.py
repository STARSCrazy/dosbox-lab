import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dosbox_lab import DmcpError, Mount, run_batch
from dosbox_lab import cli
from dosbox_lab.instance import RUNS, Instance, _pid_alive
from tests.util import TESTPROGS, need_build


def fake_instance(test, pid):
    inst_id = "fake-" + os.urandom(4).hex()
    rundir = RUNS / inst_id
    (rundir / "out").mkdir(parents=True)
    test.addCleanup(shutil.rmtree, rundir, True)
    (rundir / "instance.json").write_text(json.dumps({
        "id": inst_id, "pid": pid, "endpoint": rf"\.\pipe\dosbox-lab-{inst_id}", "mounts": []}), encoding="utf-8")
    return inst_id


class PidIdentityTest(unittest.TestCase):
    """B2: a running PID only counts if the process is dosbox-lab.exe."""

    def test_foreign_running_process_is_not_alive(self):
        self.assertFalse(_pid_alive(os.getpid()))

    def test_attach_foreign_pid_is_dead(self):
        inst_id = fake_instance(self, os.getpid())
        with self.assertRaises(DmcpError) as ctx:
            Instance.attach(inst_id)
        self.assertEqual(ctx.exception.code, "instance_dead")

    def test_stop_kills_nothing_foreign(self):
        inst_id = fake_instance(self, os.getpid())
        inst = Instance(inst_id, RUNS / inst_id, rf"\.\pipe\dosbox-lab-{inst_id}", os.getpid())
        with mock.patch("dosbox_lab.instance.subprocess.run") as run:
            inst.stop()
        run.assert_not_called()
        self.assertTrue(inst.alive() is False)  # and we are still alive

    def test_image_name_check_is_case_insensitive(self):
        with mock.patch("dosbox_lab.instance._image_name", return_value=r"C:\X\DOSBox-Lab.EXE"):
            self.assertTrue(_pid_alive(os.getpid()))
        with mock.patch("dosbox_lab.instance._image_name", return_value=None):
            self.assertFalse(_pid_alive(os.getpid()))


class StartFailureTest(unittest.TestCase):
    """B14: an OSError at startup becomes DmcpError(internal), in the CLI too as JSON."""

    def test_unexecutable_exe(self):
        tmp = Path(tempfile.mkdtemp(prefix="dmcp-exe-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "dosbox-lab.exe"
        fake.write_text("no program")
        inst_id = "failtest-" + os.urandom(3).hex()
        self.addCleanup(shutil.rmtree, RUNS / inst_id, True)
        with self.assertRaises(DmcpError) as ctx:
            Instance.start(exe=fake, inst_id=inst_id)
        self.assertEqual(ctx.exception.code, "internal")
        self.assertIn("emulator could not be started", ctx.exception.message)
        self.assertIsInstance(ctx.exception.__cause__, OSError)

    def _cli_error(self, err):
        out = []
        with mock.patch.object(Instance, "start", side_effect=err), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: out.append(a[0])):
            code = cli.main(["batch", "--dir", tempfile.gettempdir(), "--cmd", "X"])
        self.assertEqual(code, 1)
        return json.loads(out[0])

    def test_cli_prints_json_error(self):
        # This is how a start failure really arrives: Instance.start has already converted the OSError.
        data = self._cli_error(DmcpError("internal", "emulator could not be started: [WinError 32] ..."))
        self.assertEqual(data["error"], "internal")
        self.assertIn("could not be started", data["message"])

    def test_cli_other_os_error_has_neutral_message(self):
        # Other OSErrors (e.g. when reading an output file) must not show up as start failures.
        data = self._cli_error(PermissionError(32, "The process cannot access the file"))
        self.assertEqual(data["error"], "internal")
        self.assertNotIn("could not be started", data["message"])
        self.assertIn("operating system error", data["message"])


@need_build
class RelativeMountTest(unittest.TestCase):
    """B15: relative host paths are relative to the caller, not to the run folder."""

    def test_relative_mount_path(self):
        RUNS.mkdir(exist_ok=True)  # same drive as the repo, so that a relative path exists
        work = Path(tempfile.mkdtemp(prefix="dmcp-rel-", dir=RUNS))
        self.addCleanup(shutil.rmtree, work, True)
        shutil.copy(TESTPROGS / "BATCH.COM", work / "BATCH.COM")
        rel = os.path.relpath(work)
        r = run_batch([Mount("C", rel)], "BATCH", timeout_s=30)
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["exit_code"], 7)


if __name__ == "__main__":
    unittest.main()
