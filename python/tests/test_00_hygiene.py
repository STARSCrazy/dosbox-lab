import io
import unittest

from tests.util import class_box, need_build, start_idle


@need_build
class ClassBoxTest(unittest.TestCase):
    def test_cleanup_runs_when_setup_fails(self):
        class Broken(unittest.TestCase):
            box = None

            @classmethod
            def setUpClass(cls):
                cls.box = class_box(cls, start_idle())
                raise RuntimeError("setUpClass scheitert absichtlich")

            def test_nothing(self):
                pass

        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(Broken))
        self.assertEqual(len(result.errors), 1)
        self.assertFalse(Broken.box.inst.alive())
