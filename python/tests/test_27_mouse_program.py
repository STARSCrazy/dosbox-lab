import time
import unittest

from dosbox_lab import DmcpError
from dosbox_lab.api import Dosbox
from tests.util import class_box, need_build, start_prog


class FakeMouse:
    """Simuliert ein Programm mit Positionsvariablen: mouse_move_rel verschiebt die Position um
    dx * fx bzw. dy * fy (Gleitkomma intern, Ausgabe gerundet, vorzeichenlos, little endian).
    x ist auf 0..639 begrenzt, y auf 0..199."""

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
    """Wie FakeMouse, aber mit Beschleunigung: Bewegungen ab 5 Schritten zaehlen doppelt, kleinere einfach."""

    def __init__(self, x=0, y=0):
        super().__init__(x, y, 1.0, 1.0)

    def mouse_move_rel(self, dx, dy):
        self.moves += 1
        self.px = min(639.0, max(0.0, self.px + dx * (2 if abs(dx) >= 5 else 1)))
        self.py = min(199.0, max(0.0, self.py + dy * (2 if abs(dy) >= 5 else 1)))


class MouseProgramLearnTest(unittest.TestCase):
    """Ohne Emulator: prueft die Regelung und das Lernen des Faktors gegen ein simuliertes Programm."""

    def test_reaches_target_and_learns_factors(self):
        fake = FakeMouse(x=0, y=0, fx=2.0, fy=0.833)
        result = Dosbox.mouse_move_program(fake, 400, 120, "x", "y", toleranz=1, warte_s=0)
        self.assertLessEqual(abs(result["x"] - 400), 1)
        self.assertLessEqual(abs(result["y"] - 120), 1)
        self.assertAlmostEqual(result["faktor"][0], 2.0, delta=0.2)
        self.assertAlmostEqual(result["faktor"][1], 0.833, delta=0.2)
        self.assertLessEqual(result["versuche"], 6)

    def test_already_at_target_needs_no_move(self):
        fake = FakeMouse(x=400, y=120)
        result = Dosbox.mouse_move_program(fake, 400, 120, "x", "y", toleranz=1, warte_s=0)
        self.assertEqual(result["versuche"], 0)
        self.assertEqual(fake.moves, 0)

    def test_unreachable_clamped_times_out(self):
        fake = FakeMouse(x=0, y=0, fx=2.0, fy=0.833)
        with self.assertRaises(DmcpError) as ctx:
            Dosbox.mouse_move_program(fake, 5000, 120, "x", "y", versuche=6, warte_s=0)
        self.assertEqual(ctx.exception.code, "timeout")


@need_build
class MouseProgramTest(unittest.TestCase):
    """MOUSE.COM schreibt die INT-33h-Position nach 0000:04F4 (x, Modus 13h: 0..639) und 04F6 (y).
    Diese Woerter dienen als 'Programmvariablen'. Beobachteter Faktor 1,0 auf beiden Achsen:
    mouse_move_rel rechnet Pixel ueber MotionDivisor(ppm, senv) in Mickeys um (src/agent/ext/dmcp_input.cpp,
    ToMotion). Mit den Werten des Maustreibers aus MOUSE.COM ergibt das eine INT-33h-Verschiebung von
    genau dx bzw. dy; die Faktoren wurden empirisch mit einer Probebewegung gemessen (80/20/-40/-10 Pixel)."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("MOUSE.COM"))
        time.sleep(1.0)


    def test_reaches_program_position(self):
        result = self.box.mouse_move_program(400, 120, "l:4F4", "l:4F6", toleranz=1)
        self.assertLessEqual(abs(result["x"] - 400), 1)
        self.assertLessEqual(abs(result["y"] - 120), 1)
        self.assertAlmostEqual(result["faktor"][0], 1.0, delta=0.25)
        self.assertAlmostEqual(result["faktor"][1], 1.0, delta=0.25)

    def test_unreachable_times_out(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.mouse_move_program(5000, 120, "l:4F4", "l:4F6", versuche=4)
        self.assertEqual(ctx.exception.code, "timeout")


class MouseProgramStepTest(unittest.TestCase):
    """B8: Restabstand bis zum halben gelernten Schritt gilt als erreicht (mit hinweis)."""

    def test_factor_two_odd_target_reaches_with_hint(self):
        fake = FakeMouse(x=300, y=100, fx=2.0, fy=2.0)
        result = Dosbox.mouse_move_program(fake, 401, 121, "x", "y", warte_s=0)
        self.assertLessEqual(abs(result["x"] - 401), 1)
        self.assertLessEqual(abs(result["y"] - 121), 1)
        self.assertIn("hinweis", result)
        self.assertIn("Schrittweite", result["hinweis"])

    def test_acceleration_probe_reaches_exact_target(self):
        """Programm mit Beschleunigung: grosse Bewegungen zaehlen doppelt, kleine einfach. Der gelernte Faktor 2
        ist fuer den letzten Pixel zu gross; ein Probeschritt findet den echten Schritt 1."""
        fake = AccelMouse(x=300, y=100)
        result = Dosbox.mouse_move_program(fake, 401, 100, "x", "y", warte_s=0)
        self.assertEqual((result["x"], result["y"]), (401, 100))
        self.assertNotIn("hinweis", result)

    def test_factor_one_is_exact_without_hint(self):
        fake = FakeMouse(x=300, y=100, fx=1.0, fy=1.0)
        result = Dosbox.mouse_move_program(fake, 401, 121, "x", "y", warte_s=0)
        self.assertEqual((result["x"], result["y"]), (401, 121))
        self.assertNotIn("hinweis", result)
