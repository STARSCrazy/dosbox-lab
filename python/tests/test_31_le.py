import unittest

from tests.util import need_build, run_typed, start_idle, start_prog, wait_until


def module(box, name):
    return next((m for m in box.modules() if m["module"] == name), None)


def wait_le(box, name, assignment, timeout=15):
    return wait_until(lambda: (lambda m: m if m and m.get("le", {}).get("assignment") == assignment else None)(
        module(box, name)), timeout=timeout)


@need_build
class LeHeaderTest(unittest.TestCase):
    def test_objects_read(self):
        box = start_prog("LE.EXE")
        self.addCleanup(box.stop)
        m = wait_until(lambda: (lambda m: m if m and "le" in m else None)(module(box, "LE.EXE")), timeout=15)
        le = m["le"]
        objects = [(o["nr"], o["va_start"], o["size"]) for o in le["objects"]]
        self.assertEqual(objects, [(1, "00010000", "00003000"), (2, "00020000", "00000A10")])
        self.assertEqual(le["entry_va"], "00010100")
        self.assertIn(le["assignment"], ("open", "done", "abandoned"))

    def test_bad_header(self):
        box = start_prog("LEBAD.EXE")
        self.addCleanup(box.stop)
        m = wait_le(box, "LEBAD.EXE", "not_le")
        self.assertIn("hint", m["le"])
        self.assertEqual(m["le"]["objects"], [])
        self.assertEqual(m["segments"], [])
        self.assertFalse(box.status()["instruction_hook"])

    def test_no_le_field_for_com(self):
        box = start_prog("EXITCODE.COM")
        self.addCleanup(box.stop)
        m = wait_until(lambda: module(box, "EXITCODE.COM"), timeout=15)
        self.assertNotIn("le", m)
        self.assertIn("instruction_hook", box.status())


EXPECTED_ERROR_AFTER_EXEC = 0  # DOS resets the error code when the child ends; no error is left behind


def segs(m):
    return [(s["name"], s["va_start"], s["va_end"], s["base_linear"], s["origin"]) for s in m["segments"]]


@need_build
class LeDeriveTest(unittest.TestCase):
    def test_derives_both_objects(self):
        box = start_prog("LE.EXE")
        self.addCleanup(box.stop)
        m = wait_le(box, "LE.EXE", "done")
        self.assertEqual(segs(m), [("obj1", "00010000", "00012FFF", "003F0000", "derived"),
                                   ("obj2", "00020000", "00020A0F", "00400000", "derived")])
        self.assertEqual(m["le"]["entry_linear"], "00400100")
        self.assertNotIn("hint", m["le"])
        self.assertEqual(box.translate("va:LE.EXE:20004")["linear"], "00420004")
        self.assertFalse(box.status()["instruction_hook"])
        # the program ran to its end: the guest was not disturbed
        wait_until(lambda: any("LE done" in z for z in box.screen_text()["lines"]), timeout=15)

    def test_single_block_for_all(self):
        box = start_prog("LE.EXE E")
        self.addCleanup(box.stop)
        m = wait_le(box, "LE.EXE", "done")
        self.assertEqual([s[3] for s in segs(m)], ["004F0000", "004E3000"])

    def test_no_match_gives_up(self):
        box = start_prog("LE.EXE N")
        self.addCleanup(box.stop)
        m = wait_le(box, "LE.EXE", "abandoned")
        self.assertEqual(m["segments"], [])
        self.assertIn("hint", m["le"])
        self.assertFalse(box.status()["instruction_hook"])

    def test_stronger_mapping_wins(self):
        box = start_idle()
        self.addCleanup(box.stop)
        box.set_segments("LE.EXE", [{"name": "hand", "va_start": "0x10000", "va_end": "0x12FFF",
                                      "base_linear": "0x123000"}])
        run_typed(box, "LE")
        m = wait_le(box, "LE.EXE", "done")
        self.assertEqual(segs(m), [("hand", "00010000", "00012FFF", "00123000", "set")])
        self.assertIn("hint", m["le"])

    def test_rerun_derives_again(self):
        box = start_idle()
        self.addCleanup(box.stop)
        run_typed(box, "LE E")
        wait_le(box, "LE.EXE", "done")
        run_typed(box, "LE")
        m = wait_until(lambda: (lambda m: m if m and m["exec_count"] == 2 and m["le"]["assignment"] == "done"
                                else None)(module(box, "LE.EXE")), timeout=15)
        self.assertEqual([s[3] for s in segs(m)], ["003F0000", "00400000"])

    def test_rerun_without_match_clears_old_segments(self):
        box = start_idle()
        self.addCleanup(box.stop)
        run_typed(box, "LE E")
        m = wait_le(box, "LE.EXE", "done")
        self.assertEqual(len(m["segments"]), 2)
        run_typed(box, "LE N")
        m = wait_until(lambda: (lambda m: m if m and m["exec_count"] == 2 and m["le"]["assignment"] == "abandoned"
                                else None)(module(box, "LE.EXE")), timeout=15)
        self.assertEqual(m["segments"], [])


@need_build
class LeLimitsTest(unittest.TestCase):
    def test_entry_point_outside_object(self):
        box = start_prog("LEEIP.EXE")
        self.addCleanup(box.stop)
        m = wait_le(box, "LEEIP.EXE", "not_le")
        self.assertEqual(m["le"]["hint"], "LE entry point outside its object")
        self.assertEqual(m["le"]["objects"], [])
        self.assertEqual(m["segments"], [])
        self.assertFalse(box.status()["instruction_hook"])

    def test_257_requests_give_up(self):
        box = start_prog("LEVIELE.EXE")
        self.addCleanup(box.stop)
        m = wait_le(box, "LEVIELE.EXE", "abandoned")
        self.assertEqual(m["le"]["hint"], "no complete match within 256 DPMI 0501h allocations")
        self.assertEqual(m["segments"], [])
        self.assertFalse(box.status()["instruction_hook"])
        wait_until(lambda: any("LE done" in z for z in box.screen_text()["lines"]), timeout=15)

    def test_other_program_started_gives_up(self):
        box = start_prog("LEEXEC.EXE")
        self.addCleanup(box.stop)
        m = wait_le(box, "LEEXEC.EXE", "abandoned")
        self.assertEqual(m["le"]["hint"], "another program was started")
        self.assertEqual(m["segments"], [])
        self.assertIsNotNone(wait_until(lambda: module(box, "EXITCODE.COM"), timeout=15))
        self.assertFalse(box.status()["instruction_hook"])
        # the parent goes on after the child and reaches its end
        wait_until(lambda: any("LE done" in z for z in box.screen_text()["lines"]), timeout=15)


@need_build
class LeGuestErrorAfterChildTest(unittest.TestCase):
    """ERRPROBE.COM provokes error 2, starts LE.EXE (the LE header is read) and fetches INT 21h 5900h after
    the return (PSP:00F0); the same with EXITCODE.COM (no LE) at PSP:00F2."""

    def test_error_code_after_le_child_equals_non_le_child(self):
        box = start_prog("ERRPROBE.COM")
        self.addCleanup(box.stop)
        m = wait_until(lambda: module(box, "ERRPROBE.COM"), timeout=15)
        psp = int(m["psp"], 16)
        wait_until(lambda: box.mem_read(f"r:{psp:04X}:00F4", 1) == b"Z", timeout=20)
        with_le = int.from_bytes(box.mem_read(f"r:{psp:04X}:00F0", 2), "little")
        without_le = int.from_bytes(box.mem_read(f"r:{psp:04X}:00F2", 2), "little")
        le = wait_le(box, "LE.EXE", "done")  # the LE header was really read and evaluated
        self.assertEqual(le["le"]["assignment"], "done")
        self.assertEqual(with_le, without_le)
        self.assertEqual(without_le, EXPECTED_ERROR_AFTER_EXEC)


if __name__ == "__main__":
    unittest.main()
