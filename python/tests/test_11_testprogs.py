import unittest

from tests.util import need_build, run_typed, start_idle, wait_until


@need_build
class TestprogsTest(unittest.TestCase):
    def setUp(self):
        self.box = start_idle()

    def tearDown(self):
        self.box.stop()

    def lines(self):
        return [line.strip() for line in self.box.screen_text()["lines"]]

    def test_int21_runs_child_and_sets_errorlevel(self):
        run_typed(self.box, "INT21")
        wait_until(lambda: "INT21 done" in self.lines(), timeout=15)
        self.assertIn("EXITCODE stdout", self.lines())
        self.assertIn("EXITCODE stderr", self.lines())
        run_typed(self.box, "IF ERRORLEVEL 43 ECHO EL43")
        wait_until(lambda: "EL43" in self.lines(), timeout=10)
        run_typed(self.box, "IF ERRORLEVEL 44 ECHO EL44")
        run_typed(self.box, "ECHO END")
        wait_until(lambda: "END" in self.lines(), timeout=10)
        self.assertNotIn("EL44", self.lines())

    def test_flow_reaches_end(self):
        run_typed(self.box, "FLOW")
        wait_until(lambda: "FLOW done" in self.lines(), timeout=15)


if __name__ == "__main__":
    unittest.main()
