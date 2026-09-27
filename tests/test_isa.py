"""Fast unit checks for the independent ISA oracle and assembler."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from isa import ReferenceCPU, assemble, disassemble, encode, execute, expected_state


class ISATests(unittest.TestCase):
    def test_document_encodings(self):
        program = assemble("""
            ADDI r1,r0,5
            ADDI r2,r1,3
            ADD r3,r1,r2
            SW r3,0(r0)
            LW r4,0(r0)
            HALT
        """)
        self.assertEqual(program, [0x7205, 0x7443, 0x1650, 0x90C0, 0x8800, 0xF000])
        state = execute(program)
        self.assertEqual(state.registers, [0, 5, 8, 13, 13, 0, 0, 0])
        self.assertEqual(state.memory, {0: 13})
        self.assertEqual(state.retired, 6)

    def test_roundtrip_all_encodings(self):
        for word in range(65536):
            rebuilt = assemble(disassemble(word))[0]
            op = word >> 12
            mask = 0xF000 if op in (0, 15) else 0xFFF8 if 1 <= op <= 6 else 0xFFFF
            self.assertEqual(rebuilt, word & mask)

    def test_labels_and_memory_order(self):
        self.assertEqual(assemble("loop: ADDI r1,r1,1\nBNE r1,r0,loop\nHALT"),
                         [0x7241, 0xB23E, 0xF000])
        self.assertEqual(encode("SW", 3, 2, -1), 0x94FF)
        self.assertEqual(assemble(".org 2\n.word 0xffff"), [0, 0, 65535])

    def test_signed_comparison_and_overflow(self):
        state = execute(assemble("""
            ADDI r1,r0,-1
            ADDI r2,r1,1
            SLT r3,r1,r0
            SLTI r4,r1,-2
            ADDI r0,r0,31
            HALT
        """))
        self.assertEqual(state.registers, [0, 65535, 0, 1, 0, 0, 0, 0])

    def test_jalr_reads_source_before_link_write(self):
        state = execute(assemble("""
            ADDI r1,r0,3
            JALR r1,r1,1
            ADDI r2,r0,31
            HALT
            ADDI r2,r1,5
            HALT
        """))
        self.assertEqual(state.registers[:3], [0, 2, 7])
        self.assertEqual(state.retired, 4)

    def test_pause_and_reset(self):
        machine = ReferenceCPU(assemble("ADDI r1,r0,4\nSW r1,0(r0)\nHALT"))
        machine.step()
        machine.step()
        snapshot = copy.deepcopy(machine.state)
        for _ in range(10):
            self.assertFalse(machine.step(run=False))
            self.assertEqual(machine.state, snapshot)
        machine.step(rst=True)
        self.assertEqual(machine.state.registers, [0] * 8)
        self.assertEqual(machine.state.memory, {})
        self.assertEqual(machine.state.retired, 0)
        self.assertEqual(machine.run().memory, {0: 4})

    def test_rejects_invalid_operands(self):
        for source in ("ADDI r8,r0,0", "ADDI r1,r0,32", "ADDI r1,r0,-33",
                       "JAL r1,256", "JAL r1,-257", "ADD r1,r2", "MUL r1,r2,r3"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                assemble(source)

    def test_directed_oracles(self):
        path = Path(__file__).with_name("programs.json")
        if not path.exists():
            self.skipTest("directed programs have not been generated")
        suite = json.loads(path.read_text(encoding="utf-8"))
        for case in suite["programs"]:
            with self.subTest(program=case["name"]):
                self.assertEqual(assemble(case["assembly"]), case["words"])
                self.assertEqual(expected_state(case["words"], case["memory_probes"]),
                                 case["expected"])


if __name__ == "__main__":
    unittest.main()
