import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dosbox_mcp import DmcpError, Mount, run_batch
from dosbox_mcp import cli
from dosbox_mcp.instance import RUNS, Instance, _pid_alive
from tests.util import TESTPROGS, need_build


def fake_instance(test, pid):
    inst_id = "fake-" + os.urandom(4).hex()
    rundir = RUNS / inst_id
    (rundir / "out").mkdir(parents=True)
    test.addCleanup(shutil.rmtree, rundir, True)
    (rundir / "instance.json").write_text(json.dumps({
        "id": inst_id, "pid": pid, "endpoint": rf"\.\pipe\dosbox-mcp-{inst_id}", "mounts": []}), encoding="utf-8")
    return inst_id


class PidIdentityTest(unittest.TestCase):
    """B2: eine laufende PID zaehlt nur, wenn der Prozess dosbox-x.exe ist."""

    def test_foreign_running_process_is_not_alive(self):
        self.assertFalse(_pid_alive(os.getpid()))

    def test_attach_foreign_pid_is_dead(self):
        inst_id = fake_instance(self, os.getpid())
        with self.assertRaises(DmcpError) as ctx:
            Instance.attach(inst_id)
        self.assertEqual(ctx.exception.code, "instance_dead")

    def test_stop_kills_nothing_foreign(self):
        inst_id = fake_instance(self, os.getpid())
        inst = Instance(inst_id, RUNS / inst_id, rf"\.\pipe\dosbox-mcp-{inst_id}", os.getpid())
        with mock.patch("dosbox_mcp.instance.subprocess.run") as run:
            inst.stop()
        run.assert_not_called()
        self.assertTrue(inst.alive() is False)  # und wir leben noch

    def test_image_name_check_is_case_insensitive(self):
        with mock.patch("dosbox_mcp.instance._image_name", return_value=r"C:\X\DOSBox-X.EXE"):
            self.assertTrue(_pid_alive(os.getpid()))
        with mock.patch("dosbox_mcp.instance._image_name", return_value=None):
            self.assertFalse(_pid_alive(os.getpid()))


class StartFailureTest(unittest.TestCase):
    """B14: OSError beim Start wird DmcpError(internal), auch im CLI als JSON."""

    def test_unexecutable_exe(self):
        tmp = Path(tempfile.mkdtemp(prefix="dmcp-exe-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "dosbox-x.exe"
        fake.write_text("kein programm")
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
        # So kommt ein Startfehler wirklich an: Instance.start hat den OSError schon umgewandelt.
        data = self._cli_error(DmcpError("internal", "emulator could not be started: [WinError 32] ..."))
        self.assertEqual(data["fehler"], "internal")
        self.assertIn("could not be started", data["meldung"])

    def test_cli_other_os_error_has_neutral_message(self):
        # Andere OSErrors (z. B. beim Lesen einer Ausgabedatei) duerfen nicht als Startfehler erscheinen.
        data = self._cli_error(PermissionError(32, "Der Prozess kann nicht auf die Datei zugreifen"))
        self.assertEqual(data["fehler"], "internal")
        self.assertNotIn("could not be started", data["meldung"])
        self.assertIn("operating system error", data["meldung"])


@need_build
class RelativeMountTest(unittest.TestCase):
    """B15: relative Host-Pfade gelten relativ zum Aufrufer, nicht zum Laufordner."""

    def test_relative_mount_path(self):
        RUNS.mkdir(exist_ok=True)  # gleiches Laufwerk wie das Repo, damit ein relativer Pfad existiert
        work = Path(tempfile.mkdtemp(prefix="dmcp-rel-", dir=RUNS))
        self.addCleanup(shutil.rmtree, work, True)
        shutil.copy(TESTPROGS / "BATCH.COM", work / "BATCH.COM")
        rel = os.path.relpath(work)
        r = run_batch([Mount("C", rel)], "BATCH", timeout_s=30)
        self.assertEqual(r["status"], "fertig")
        self.assertEqual(r["exit_code"], 7)


if __name__ == "__main__":
    unittest.main()
