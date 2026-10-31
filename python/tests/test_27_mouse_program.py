import time
import unittest

from dosbox_lab import DmcpError
from dosbox_lab.api import Dosbox
from tests.util import class_box, need_build, start_prog


class FakeMouse:
    """Simulates a program with position variables: mouse_move_rel shifts the position by
    dx * fx and dy * fy (floating point internally, output rounded, unsigned, little endian).
    x is clamped to 0..639, y to 0..199."""

    def __init__(self, x=0, y=0, fx=2.0, fy=0.833):
        self.px, self.py, self.fx, self.fy = float(x), float(y), fx, fy
        self.moves = 0

    def mem_read(self, addr, length):
        value = round(self.px) if addr == "x" else round(self.py)
        return int(value).to_bytes(length, "little")

    def mouse_move_rel(self, dx, dy):
        self.moves += 1
        self.px = min(639.0, max(0.0, self.px + dx * self.fx))
        self.py = min(199.0, max(0.0, self.py + dy * self.fy))


class AccelMouse(FakeMouse):
    """Like FakeMouse, but with acceleration: moves of 5 or more steps count twice, smaller ones once."""

    def __init__(self, x=0, y=0):
        super().__init__(x, y, 1.0, 1.0)

    def mouse_move_rel(self, dx, dy):
        self.moves += 1
        self.px = min(639.0, max(0.0, self.px + dx * (2 if abs(dx) >= 5 else 1)))
        self.py = min(199.0, max(0.0, self.py + dy * (2 if abs(dy) >= 5 else 1)))


class MouseProgramLearnTest(unittest.TestCase):
    """Without an emulator: checks the control loop and the factor learning against a simulated program."""

    def test_reaches_target_and_learns_factors(self):
        fake = FakeMouse(x=0, y=0, fx=2.0, fy=0.833)
        result = Dosbox.mouse_move_program(fake, 400, 120, "x", "y", tolerance=1, wait_s=0)
        self.assertLessEqual(abs(result["x"] - 400), 1)
        self.assertLessEqual(abs(result["y"] - 120), 1)
        self.assertAlmostEqual(result["factor"][0], 2.0, delta=0.2)
        self.assertAlmostEqual(result["factor"][1], 0.833, delta=0.2)
        self.assertLessEqual(result["attempts"], 6)

    def test_already_at_target_needs_no_move(self):
        fake = FakeMouse(x=400, y=120)
        result = Dosbox.mouse_move_program(fake, 400, 120, "x", "y", tolerance=1, wait_s=0)
        self.assertEqual(result["attempts"], 0)
        self.assertEqual(fake.moves, 0)

    def test_unreachable_clamped_times_out(self):
        fake = FakeMouse(x=0, y=0, fx=2.0, fy=0.833)
        with self.assertRaises(DmcpError) as ctx:
            Dosbox.mouse_move_program(fake, 5000, 120, "x", "y", attempts=6, wait_s=0)
        self.assertEqual(ctx.exception.code, "timeout")


@need_build
class MouseProgramTest(unittest.TestCase):
    """MOUSE.COM writes the INT 33h position to 0000:04F4 (x, mode 13h: 0..639) and 04F6 (y).
    These words serve as 'program variables'. Observed factor 1.0 on both axes:
    mouse_move_rel converts pixels to mickeys via MotionDivisor(ppm, senv) (src/agent/ext/dmcp_input.cpp,
    ToMotion). With the values of the mouse driver in MOUSE.COM this gives an INT 33h shift of
    exactly dx and dy; the factors were measured empirically with a trial move (80/20/-40/-10 pixels)."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("MOUSE.COM"))
        time.sleep(1.0)


    def test_reaches_program_position(self):
        result = self.box.mouse_move_program(400, 120, "l:4F4", "l:4F6", tolerance=1)
        self.assertLessEqual(abs(result["x"] - 400), 1)
        self.assertLessEqual(abs(result["y"] - 120), 1)
        self.assertAlmostEqual(result["factor"][0], 1.0, delta=0.25)
        self.assertAlmostEqual(result["factor"][1], 1.0, delta=0.25)

    def test_unreachable_times_out(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.mouse_move_program(5000, 120, "l:4F4", "l:4F6", attempts=4)
        self.assertEqual(ctx.exception.code, "timeout")


class MouseProgramStepTest(unittest.TestCase):
    """B8: A remaining distance up to half the learned step counts as reached (with a hint)."""

    def test_factor_two_odd_target_reaches_with_hint(self):
        fake = FakeMouse(x=300, y=100, fx=2.0, fy=2.0)
        result = Dosbox.mouse_move_program(fake, 401, 121, "x", "y", wait_s=0)
        self.assertLessEqual(abs(result["x"] - 401), 1)
        self.assertLessEqual(abs(result["y"] - 121), 1)
        self.assertIn("hint", result)
        self.assertIn("step size", result["hint"])

    def test_acceleration_probe_reaches_exact_target(self):
        """Program with acceleration: large moves count twice, small ones once. The learned factor 2
        is too large for the last pixel; a trial step finds the real step of 1."""
        fake = AccelMouse(x=300, y=100)
        result = Dosbox.mouse_move_program(fake, 401, 100, "x", "y", wait_s=0)
        self.assertEqual((result["x"], result["y"]), (401, 100))
        self.assertNotIn("hint", result)

    def test_factor_one_is_exact_without_hint(self):
        fake = FakeMouse(x=300, y=100, fx=1.0, fy=1.0)
        result = Dosbox.mouse_move_program(fake, 401, 121, "x", "y", wait_s=0)
        self.assertEqual((result["x"], result["y"]), (401, 121))
        self.assertNotIn("hint", result)
