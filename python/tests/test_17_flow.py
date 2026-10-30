import unittest

from dosbox_mcp.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, start_prog, wait_prompt, wait_until


def flow_psp(box):
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == "FLOW.COM"), None), timeout=15)
    psp = int(module["psp"], 16)
    wait_until(lambda: box.mem_read(f"r:{psp:04X}:01F0", 1) == b"\x01", timeout=15)
    return psp


INDIRECT = {("call_near", 0x107, 0x160), ("call_near", 0x109, 0x168), ("call_near", 0x10F, 0x160),
            ("jmp_near", 0x111, 0x140), ("call_far", 0x140, 0x170)}
RETURNS = {("ret", 0x160, 0x109), ("ret", 0x168, 0x10C), ("ret", 0x160, 0x111), ("retf", 0x170, 0x144)}


class FlowHelpers:
    def own(self, entries):
        lo, hi = self.psp * 16 + 0x100, self.psp * 16 + 0x200
        return [e for e in entries if lo <= int(e["quelle"]["linear"], 16) < hi]

    def pairs_of(self, entries):
        base = self.psp * 16
        return {(e["art"], int(e["quelle"]["linear"], 16) - base, int(e["ziel"]["linear"], 16) - base)
                for e in self.own(entries)}


@need_build
class FlowTest(unittest.TestCase, FlowHelpers):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)  # Start-Stub der Shell (liegt im selben Segment wie FLOW) soll fertig sein
        cls.box.flow_start(mit_returns=True, ring_groesse=100000)
        cls.box.coverage_start(hits=True)
        run_typed(cls.box, "FLOW")
        cls.psp = flow_psp(cls.box)
        # fertig=1 steht schon vor INT 21h und HLT; der Sprung bei 0152 laeuft erst nach dem ersten Timer-IRQ.
        hlt_jmp = cls.psp * 16 + 0x152
        wait_until(lambda: any(e["art"] == "jmp" and int(e["quelle"]["linear"], 16) == hlt_jmp for e in
                               read_jsonl(cls.box.flow_recent(100000, "rec_flow")["pfad"])), timeout=15)
        cls.pairs = read_jsonl(cls.box.flow_save("ind_flow")["pfad"])
        cls.recent = read_jsonl(cls.box.flow_recent(100000, "rec_flow")["pfad"])
        cls.hits = read_jsonl(cls.box.coverage_save("cov_hits")["hits_pfad"])


    def test_indirect_pairs_with_returns(self):
        self.assertEqual(self.pairs_of(self.pairs), INDIRECT | RETURNS)

    def test_pair_fields(self):
        far = next(e for e in self.own(self.pairs) if e["art"] == "call_far")
        self.assertEqual(far["anzahl"], 1)
        self.assertEqual(far["bits"], 16)
        self.assertEqual(far["quelle"]["rel"], "0000:0140")
        self.assertEqual(far["ziel"]["modul"], "FLOW.COM")
        self.assertIsInstance(far["erstes_nr"], int)
        nrs = [e["erstes_nr"] for e in self.pairs]
        self.assertEqual(nrs, sorted(nrs))

    def test_ring_order(self):
        base = self.psp * 16
        # Ein Timer-IRQ kann zufaellig mitten in FLOW fallen; er gehoert nicht zur erwarteten Folge.
        seq = [(e["art"], int(e["quelle"]["linear"], 16) - base) for e in self.own(self.recent)
               if e["art"] not in ("irq", "exception")]
        self.assertEqual(seq[:10], [("call_near", 0x107), ("ret", 0x160), ("call_near", 0x109), ("ret", 0x168),
                                    ("call_near", 0x10F), ("ret", 0x160), ("jmp_near", 0x111),
                                    ("call_far", 0x140), ("retf", 0x170), ("int", 0x14E)])
        self.assertEqual(next(e for e in self.own(self.recent) if e["art"] == "int")["vektor"], "0x21")

    def test_hlt_loop_in_ring(self):
        base = self.psp * 16
        jumps = [e for e in self.own(self.recent) if e["art"] == "jmp"]
        self.assertTrue(jumps)
        self.assertTrue(all(int(e["ziel"]["linear"], 16) - base == 0x151 for e in jumps))

    def test_basic_block_hits(self):
        base = self.psp * 16
        hits = {int(h["linear"], 16) - base: h["treffer"] for h in self.hits
                if base + 0x100 <= int(h["linear"], 16) < base + 0x200}
        self.assertEqual(hits[0x160], 2)
        self.assertEqual(hits[0x168], 1)
        self.assertEqual(hits[0x140], 1)
        self.assertGreaterEqual(hits[0x151], 1)
        self.assertNotIn(0x104, hits)


@need_build
class FlowBreakpointTest(unittest.TestCase, FlowHelpers):
    def test_breakpoint_resume_records_once(self):
        probe = start_idle()  # gleiche Config -> gleiches PSP fuer FLOW
        try:
            run_typed(probe, "FLOW")
            self.psp = flow_psp(probe)
        finally:
            probe.stop()
        box = start_idle()
        try:
            wait_prompt(box)
            box.break_add(self.psp * 16 + 0x107)
            box.flow_start(mit_returns=False, ring_groesse=100000)
            run_typed(box, "FLOW")
            status = box.wait_paused(15)
            self.assertEqual(status["stop_reason"], "breakpoint")
            box.step(1)
            box.resume()
            flow_psp(box)
            pairs = read_jsonl(box.flow_save("ind_bp")["pfad"])
            recent = read_jsonl(box.flow_recent(100000, "rec_bp")["pfad"])
            self.assertEqual(self.pairs_of(pairs), INDIRECT)
            self.assertEqual(next(e for e in self.own(pairs) if e["art"] == "call_near"
                                  and e["quelle"]["rel"] == "0000:0107")["anzahl"], 1)
            self.assertFalse([e for e in pairs if e["quelle"]["linear"] == e["ziel"]["linear"]])
            first = [(e["art"], e["quelle"]["rel"]) for e in self.own(recent) if e["art"] not in ("irq", "exception")][:2]
            self.assertEqual(first, [("call_near", "0000:0107"), ("ret", "0000:0160")])
        finally:
            box.stop()

    def test_idle_calls(self):
        box = start_idle()
        try:
            self.assertEqual(read_jsonl(box.flow_save("leer")["pfad"]), [])
            box.flow_stop()
            self.assertFalse(box.flow_status()["aktiv"])
        finally:
            box.stop()


@need_build
class Flow32Test(unittest.TestCase):
    """PMODE.COM: die Endlosschleife im 32-Bit-Teil (CS=0008) springt mit jmp short zurueck."""

    def test_32_bit_jump_recorded(self):
        box = start_prog("PMODE.COM")
        self.addCleanup(box.stop)
        wait_until(lambda: box.registers()["paging"], timeout=15)
        box.flow_start(ring_groesse=1000)
        jumps = wait_until(lambda: [e for e in read_jsonl(box.flow_recent(50, "flow32")["pfad"])
                                    if e["art"] == "jmp"], timeout=15)
        for e in jumps:
            self.assertEqual(e["bits"], 32)
            self.assertEqual(e["quelle"]["bits"], 32)
            self.assertEqual(e["quelle"]["sel"], "0x0008")
            self.assertEqual(e["quelle"]["modus"], "pm")
            self.assertLess(int(e["ziel"]["linear"], 16), int(e["quelle"]["linear"], 16))  # zurueck zum Schleifenanfang


if __name__ == "__main__":
    unittest.main()
