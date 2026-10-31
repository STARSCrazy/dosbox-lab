import json
import subprocess
import sys
import unittest
from pathlib import Path

from dosbox_lab import mcp_server
from tests.util import TESTPROGS, batch_workdir, need_build

PYDIR = Path(__file__).resolve().parents[1]


@need_build
class BatchToolsTest(unittest.TestCase):
    def test_cli_batch(self):
        work = batch_workdir(self)
        proc = subprocess.run([sys.executable, "-m", "dosbox_lab", "batch", "--dir", str(work), "--cmd", "BATCH",
                               "--timeout", "30"], cwd=PYDIR, capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual((result["status"], result["exit_code"]), ("done", 7))

    def test_cli_batch_bad_argument(self):
        proc = subprocess.run([sys.executable, "-m", "dosbox_lab", "batch", "--dir", str(TESTPROGS), "--cmd", " "],
                              cwd=PYDIR, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"], "bad_argument")

    def test_mcp_tool_with_cdrom_d(self):
        work = batch_workdir(self)
        result = mcp_server.run_batch(drive_c=str(work), command="D:\\EXITCODE", timeout_s=30,
                                      drive_d=str(TESTPROGS), cdrom_d=True)
        self.assertEqual((result["status"], result["exit_code"]), ("done", 0x2A))
        # TESTPROG-TEXT (EXITCODE.COM)
        self.assertEqual(result["stdout_text"], "EXITCODE stdout\r\n")
        self.assertEqual(result["stderr_text"], "EXITCODE stderr\r\n")
        self.assertEqual(result["files"], [])  # D: is a CD (not compared), C: unchanged


if __name__ == "__main__":
    unittest.main()
