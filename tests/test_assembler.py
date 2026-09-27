"""Regression tests for the user-facing Superscalar16 assembler."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from assembler import AssemblyError, assemble_file, assemble_source, format_raw, main


class AssemblerTests(unittest.TestCase):
    def test_program_labels_pseudo_instructions_and_comments(self):
        result = assemble_source(
            """
                COUNT = 3
                .text
            start:  LI r1, COUNT       ; r1 = 3
                    MOV r2, r1
            loop:   ADDI r2, r2, -1
                    BNEZ r2, loop      // label is relative to PC+1
                    CALL done
                    NOP
            done:   HALT # finished
            """,
            "program.s",
        )
        self.assertEqual(
            result.text,
            [0x7203, 0x7440, 0x74BF, 0xB43E, 0xCE01, 0x0000, 0xF000],
        )
        self.assertEqual(result.symbols["LOOP"].value, 2)
        self.assertEqual(result.symbols["DONE"].value, 6)
        self.assertEqual(result.symbols["COUNT"].value, 3)

    def test_data_directives_have_an_independent_address_space(self):
        result = assemble_source(
            r'''
                COUNT = 3
                .text
                HALT
                .data
                .org 2
            values: .word 0x1234, -1
                .fill COUNT, values + 1
                .align 8, 0xbeef
                .asciz "A\n"
            ''',
            "data.s",
        )
        self.assertEqual(result.text, [0xF000])
        self.assertEqual(
            result.data,
            [0, 0, 0x1234, 0xFFFF, 3, 3, 3, 0xBEEF, ord("A"), ord("\n"), 0],
        )
        self.assertEqual(result.symbols["VALUES"].section, "data")

    def test_native_and_parenthesized_memory_syntax(self):
        result = assemble_source("LW r1, 4(r2)\nSW r3, r2, -1\nJALR r7, 2(r1)\nHALT")
        self.assertEqual(result.text, [0x8284, 0x94FF, 0xDE42, 0xF000])

    def test_li_builds_any_16_bit_constant_without_new_isa_opcodes(self):
        result = assemble_source(
            "LI r1, 0xF000\nLI r2, 0x1234\nLI r3, -1\nHALT\nafter: NOP",
            "li.s",
        )
        self.assertEqual(len(result.text), 29)
        # Every expansion is ADDI/ADD only; execute it through the ISA oracle.
        self.assertEqual(result.text[-1], 0x0000)
        self.assertEqual(result.symbols["AFTER"].value, 28)
        self.assertEqual(result.text[0] >> 12, 7)
        self.assertEqual(result.text[13] >> 12, 7)
        self.assertEqual(result.text[26] >> 12, 7)

        # Put the generated words in a terminating program and check values.
        from isa import ReferenceCPU
        machine = ReferenceCPU(result.text)
        # The test source has HALT at address 27; after it is a NOP label.
        machine.run()
        self.assertEqual(machine.state.registers[:4], [0, 0xF000, 0x1234, 0xFFFF])

    def test_forward_label_li_reserves_long_expansion(self):
        result = assemble_source("LI r1, target\nB target\ntarget: HALT")
        self.assertEqual(result.symbols["TARGET"].value, 14)
        self.assertEqual(len(result.text), 15)

    def test_current_address_and_backward_org_overlap_diagnostic(self):
        self.assertEqual(assemble_source(".word $, $+1").text, [0, 1])
        with self.assertRaisesRegex(AssemblyError, "written more than once"):
            assemble_source(".word 1\n.org 0\n.word 2", "overlap.s")

    def test_precise_diagnostics(self):
        cases = {
            "ADDI r8,r0,0": "invalid register",
            "ADDI r1,r0,32": "signed 6-bit immediate out of range",
            "B missing": "unknown symbol",
            ".data\nvalue: .word 1\n.text\nB value": "is in .data",
            ".align 3": "positive power of two",
            "x: NOP\nx: HALT": "duplicate symbol",
        }
        for source, message in cases.items():
            with self.subTest(source=source):
                with self.assertRaises(AssemblyError) as caught:
                    assemble_source(source, "bad.s")
                rendered = str(caught.exception)
                self.assertIn("bad.s:", rendered)
                self.assertIn(message, rendered)

    def test_include_and_cli_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "constants.inc").write_text("VALUE = 5\n", encoding="utf-8")
            source = directory / "main.s"
            source.write_text(
                '.include "constants.inc"\nLI r1, VALUE\nHALT\n.data\n.word 0x55aa\n',
                encoding="utf-8",
            )
            self.assertEqual(assemble_file(source).text, [0x7205, 0xF000])

            output = directory / "program.hex"
            data = directory / "initial.hex"
            listing = directory / "program.lst"
            symbols = directory / "program.sym"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = main([
                    str(source), "-o", str(output), "-d", str(data),
                    "-l", str(listing), "--symbols", str(symbols),
                ])
            self.assertEqual(status, 0, stderr.getvalue())
            self.assertEqual(output.read_text(encoding="utf-8"), "v2.0 raw\n7205 f000\n")
            self.assertEqual(data.read_text(encoding="utf-8"), "v2.0 raw\n55aa\n")
            self.assertIn("LI r1, VALUE", listing.read_text(encoding="utf-8"))
            self.assertIn("VALUE", symbols.read_text(encoding="utf-8"))
            self.assertIn("assembled 2 text word(s)", stdout.getvalue())

    def test_format_raw_rejects_non_words(self):
        self.assertEqual(format_raw([0, 0xFFFF]), "v2.0 raw\n0000 ffff\n")
        with self.assertRaises(ValueError):
            format_raw([0x10000])


if __name__ == "__main__":
    unittest.main()
